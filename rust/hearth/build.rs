//! Puts Hearth's icon and version information into `Hearth.exe` (what the Python build wrote
//! with `pack/write_version_info.py`), so Explorer, the taskbar and Setup show them.

fn main() {
    println!("cargo::rerun-if-changed=../../pack/hearth.ico");
    remedy_ui_build::WindowsResources::new()
        .icon("../../pack/hearth.ico")
        .product_name("Hearth")
        .file_description("Hearth - one simple home for sharing")
        .company("Ahmi Darrow")
        .copyright("Copyright (c) 2026 Ahmi Darrow")
        .executable("Hearth")
        .compile()
        .unwrap_or_else(|e| println!("cargo::warning={e}"));
}
