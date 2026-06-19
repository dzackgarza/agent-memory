// In-process reimplementation of the iwe CLI subcommands the vault uses, calling
// liwe's own operations (the same functions the `iwe` binary invokes) and persisting
// the resulting `Changes` exactly as the CLI's `apply_changes` does. Each function loads
// the graph from the vault root (no .iwe/ config), runs the upstream operation, writes
// the changes back to disk, and returns the value the Python layer expects.
//
// Mirrors iwe-org/iwe rev 71d5fb3 (v0.3.2): crates/iwe/src/main.rs load_graph /
// apply_changes / squash_command / extract_command / inline_command, and
// crates/iwe/src/render.rs for retrieve output (vendored in render.rs).

use std::path::Path;
use std::time::SystemTime;

use liwe::graph::{Graph, GraphContext};
use liwe::model::config::MarkdownOptions;
use liwe::model::node::Node;
use liwe::model::tree::{Tree as ModelTree, TreeIter};
use liwe::model::{Key, NodeId};
use liwe::operations::{
    delete as liwe_delete, extract as liwe_extract, inline as liwe_inline, rename as liwe_rename,
    Changes, ExtractConfig, InlineConfig, OperationError,
};
use liwe::retrieve::{DocumentReader, RetrieveOptions};

use crate::render::RetrieveRenderer;

/// Error surfaced to Python. The string is the human-readable failure; the Python layer
/// raises it loudly (no fallback) so a missing key or ambiguous section fails the command.
pub enum IweError {
    NotFound(String),
    Operation(String),
}

impl From<OperationError> for IweError {
    fn from(err: OperationError) -> Self {
        IweError::Operation(err.to_string())
    }
}

fn markdown_options() -> MarkdownOptions {
    // The vault stores plain markdown notes at the vault root with default markdown
    // options, matching the iwe binary run with no project config (library.path = "").
    MarkdownOptions::default()
}

fn load_graph(vault: &Path) -> Graph {
    Graph::from_path(vault, false, markdown_options(), None)
}

/// Persist a `Changes` to disk under `vault`, mirroring the CLI's `apply_changes`:
/// remove deleted files (and prune now-empty parent directories), write creates
/// (creating parent dirs), then write updates.
fn apply_changes(changes: &Changes, vault: &Path) {
    for key in &changes.removes {
        let file_path = vault.join(format!("{}.md", key));
        if file_path.exists() {
            std::fs::remove_file(&file_path).expect("Failed to delete document file");
        }
        let mut dir = file_path.parent().map(|p| p.to_path_buf());
        while let Some(parent) = dir {
            if parent == vault || !parent.starts_with(vault) {
                break;
            }
            if parent.read_dir().map_or(false, |mut d| d.next().is_none()) {
                let _ = std::fs::remove_dir(&parent);
                dir = parent.parent().map(|p| p.to_path_buf());
            } else {
                break;
            }
        }
    }

    for (key, markdown) in &changes.creates {
        let file_path = vault.join(format!("{}.md", key));
        if let Some(parent) = file_path.parent() {
            std::fs::create_dir_all(parent).ok();
        }
        std::fs::write(&file_path, markdown).expect("Failed to write document file");
    }

    for (key, markdown) in &changes.updates {
        let file_path = vault.join(format!("{}.md", key));
        std::fs::write(&file_path, markdown).expect("Failed to write document file");
    }
}

/// `iwe retrieve -k <key>` markdown output (RetrieveRenderer over default options).
pub fn retrieve(vault: &Path, key: &str) -> Result<String, IweError> {
    let graph = load_graph(vault);
    let target = Key::name(key);
    if (&graph).get_node_id(&target).is_none() {
        return Err(IweError::NotFound(key.to_string()));
    }
    let reader = DocumentReader::new(&graph);
    let options = RetrieveOptions::default();
    let output = reader.retrieve_many(&[target], &options);
    let markdown_options = graph.markdown_options();
    let renderer = RetrieveRenderer::new(&output, &markdown_options, &graph);
    Ok(renderer.render())
}

/// `iwe squash <key> --depth <depth>` markdown output.
pub fn squash(vault: &Path, key: &str, depth: u8) -> Result<String, IweError> {
    let graph = load_graph(vault);
    let target = Key::name(key);
    if (&graph).get_node_id(&target).is_none() {
        return Err(IweError::NotFound(key.to_string()));
    }
    let squashed = (&graph).squash(&target, depth);
    let mut patch = Graph::new();
    patch.build_key_from_iter(&target, TreeIter::new(&squashed));
    Ok(patch.export_key(&target).unwrap_or_default())
}

/// `iwe rename <old> <new>`: rewrites all referencing links and moves the document.
pub fn rename(vault: &Path, old_key: &str, new_key: &str) -> Result<(), IweError> {
    let graph = load_graph(vault);
    let old = Key::name(old_key);
    let new = Key::name(new_key);
    let changes = liwe_rename(&graph, &old, &new)?;
    apply_changes(&changes, vault);
    Ok(())
}

/// `iwe delete <key>`: removes the document and cleans references to it.
pub fn delete(vault: &Path, key: &str) -> Result<(), IweError> {
    let graph = load_graph(vault);
    let target = Key::name(key);
    let changes = liwe_delete(&graph, &target)?;
    apply_changes(&changes, vault);
    Ok(())
}

fn collect_sections(tree: &ModelTree, sections: &mut Vec<(String, Option<NodeId>)>) {
    if let Node::Section(inlines) = &tree.node {
        let title = inlines.iter().map(|i| i.plain_text()).collect::<String>();
        sections.push((title, tree.id));
    }
    for child in &tree.children {
        collect_sections(child, sections);
    }
}

fn collect_references(tree: &ModelTree, refs: &mut Vec<(String, Key, Option<NodeId>)>) {
    if let Node::Reference(reference) = &tree.node {
        refs.push((reference.text.clone(), reference.key.clone(), tree.id));
    }
    for child in &tree.children {
        collect_references(child, refs);
    }
}

/// `iwe extract <key> --section <section> -f keys`: extract the matching section into a
/// new note and return the affected keys (the printed `-f keys` output). The section is
/// resolved by case-insensitive substring match on its title, exactly as the CLI does;
/// an empty or ambiguous match is a loud error.
pub fn extract(vault: &Path, key: &str, section: &str) -> Result<Vec<String>, IweError> {
    let graph = load_graph(vault);
    let source_key = Key::name(key);
    if (&graph).get_node_id(&source_key).is_none() {
        return Err(IweError::NotFound(key.to_string()));
    }
    let tree = (&graph).collect(&source_key);
    let mut sections: Vec<(String, Option<NodeId>)> = Vec::new();
    collect_sections(&tree, &mut sections);

    let needle = section.to_lowercase();
    let matches: Vec<&(String, Option<NodeId>)> = sections
        .iter()
        .filter(|(title, _)| title.to_lowercase().contains(&needle))
        .collect();
    if matches.is_empty() {
        return Err(IweError::Operation(format!(
            "No section matches '{}'",
            section
        )));
    }
    if matches.len() > 1 {
        let titles: Vec<String> = matches.iter().map(|(title, _)| title.clone()).collect();
        return Err(IweError::Operation(format!(
            "Multiple sections match '{}': {}",
            section,
            titles.join(", ")
        )));
    }
    let section_id = matches[0]
        .1
        .expect("matched section must carry a NodeId from the collected tree");

    let config = extract_config();
    let changes = liwe_extract(&graph, &source_key, section_id, &config, SystemTime::now())?;
    let affected: Vec<String> = changes
        .affected_keys()
        .iter()
        .map(|k| k.to_string())
        .collect();
    apply_changes(&changes, vault);
    Ok(affected)
}

fn extract_config() -> ExtractConfig {
    // The iwe binary's default extract action keys notes by the section slug and links
    // them with a markdown link. key_date_format/locale only matter for date-templated
    // keys; the vault uses the slug template, so the defaults are inert but required.
    ExtractConfig {
        key_template: "{{slug}}".to_string(),
        link_type: Some(liwe::model::config::LinkType::Markdown),
        key_date_format: liwe::model::config::DEFAULT_KEY_DATE_FORMAT.to_string(),
        locale: liwe::locale::get_locale(None),
    }
}

/// `iwe inline <key> --reference <reference> -f keys`: inline the referenced note back
/// into the source and return the affected keys. The reference is resolved by
/// case-insensitive substring match on its link text or its target key, exactly as the
/// CLI does; empty or ambiguous matches are loud errors.
pub fn inline(vault: &Path, key: &str, reference: &str) -> Result<Vec<String>, IweError> {
    let graph = load_graph(vault);
    let source_key = Key::name(key);
    if (&graph).get_node_id(&source_key).is_none() {
        return Err(IweError::NotFound(key.to_string()));
    }
    let tree = (&graph).collect(&source_key);
    let mut refs: Vec<(String, Key, Option<NodeId>)> = Vec::new();
    collect_references(&tree, &mut refs);

    let needle = reference.to_lowercase();
    let matches: Vec<&(String, Key, Option<NodeId>)> = refs
        .iter()
        .filter(|(text, ref_key, _)| {
            text.to_lowercase().contains(&needle)
                || ref_key.to_string().to_lowercase().contains(&needle)
        })
        .collect();
    if matches.is_empty() {
        return Err(IweError::Operation(format!(
            "No reference matches '{}'",
            reference
        )));
    }
    if matches.len() > 1 {
        let described: Vec<String> = matches
            .iter()
            .map(|(text, ref_key, _)| format!("[{}]({})", text, ref_key))
            .collect();
        return Err(IweError::Operation(format!(
            "Multiple references match '{}': {}",
            reference,
            described.join(", ")
        )));
    }
    let ref_id = matches[0]
        .2
        .expect("matched reference must carry a NodeId from the collected tree");

    let config = InlineConfig {
        inline_type: liwe::model::config::InlineType::Section,
        keep_target: false,
    };
    let changes = liwe_inline(&graph, &source_key, ref_id, &config)?;
    let affected: Vec<String> = changes
        .affected_keys()
        .iter()
        .map(|k| k.to_string())
        .collect();
    apply_changes(&changes, vault);
    Ok(affected)
}
