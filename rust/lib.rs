// PyO3 module `agent_memory._iwe`: in-process binding to the `liwe` library (the same
// implementation the `iwe` binary uses). The Python operations layer calls these
// functions instead of shelling out to the `iwe` CLI.

use pyo3::exceptions::{PyKeyError, PyRuntimeError};
use pyo3::prelude::*;

mod ops;
mod render;

use ops::IweError;

fn to_py_err(err: IweError) -> PyErr {
    match err {
        IweError::NotFound(key) => PyKeyError::new_err(format!("memory key does not exist: {}", key)),
        IweError::Operation(message) => PyRuntimeError::new_err(message),
    }
}

/// `iwe retrieve -k <key>` markdown for a memory.
#[pyfunction]
fn retrieve(vault: &str, key: &str) -> PyResult<String> {
    ops::retrieve(std::path::Path::new(vault), key).map_err(to_py_err)
}

/// `iwe squash <key> --depth <depth>` markdown for a memory subtree.
#[pyfunction]
fn squash(vault: &str, key: &str, depth: u8) -> PyResult<String> {
    ops::squash(std::path::Path::new(vault), key, depth).map_err(to_py_err)
}

/// `iwe rename <old> <new>`: move a memory and rewrite every referencing link.
#[pyfunction]
fn rename(vault: &str, old_key: &str, new_key: &str) -> PyResult<()> {
    ops::rename(std::path::Path::new(vault), old_key, new_key).map_err(to_py_err)
}

/// `iwe delete <key>`: remove a memory and clean references to it.
#[pyfunction]
fn delete(vault: &str, key: &str) -> PyResult<()> {
    ops::delete(std::path::Path::new(vault), key).map_err(to_py_err)
}

/// `iwe extract <key> --section <section> -f keys`: extract a section into a new memory,
/// returning the affected keys.
#[pyfunction]
fn extract(vault: &str, key: &str, section: &str) -> PyResult<Vec<String>> {
    ops::extract(std::path::Path::new(vault), key, section).map_err(to_py_err)
}

/// `iwe inline <key> --reference <reference> -f keys`: inline a referenced memory back
/// into the source, returning the affected keys.
#[pyfunction]
fn inline(vault: &str, key: &str, reference: &str) -> PyResult<Vec<String>> {
    ops::inline(std::path::Path::new(vault), key, reference).map_err(to_py_err)
}

#[pymodule]
fn _iwe(module: &Bound<'_, PyModule>) -> PyResult<()> {
    module.add_function(wrap_pyfunction!(retrieve, module)?)?;
    module.add_function(wrap_pyfunction!(squash, module)?)?;
    module.add_function(wrap_pyfunction!(rename, module)?)?;
    module.add_function(wrap_pyfunction!(delete, module)?)?;
    module.add_function(wrap_pyfunction!(extract, module)?)?;
    module.add_function(wrap_pyfunction!(inline, module)?)?;
    Ok(())
}
