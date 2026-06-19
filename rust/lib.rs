use pyo3::prelude::*;

/// De-risk probe: a trivial function proving the binding builds and imports.
#[pyfunction]
fn liwe_revision() -> &'static str {
    "71d5fb3e08a32f8f49a6f12f650bd780e4c633c9"
}

#[pymodule]
fn _iwe(module: &Bound<'_, PyModule>) -> PyResult<()> {
    module.add_function(wrap_pyfunction!(liwe_revision, module)?)?;
    Ok(())
}
