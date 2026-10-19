fn main() {
    tauri_build::build();

    // Guard against silently producing a release-profile executable that depends
    // on the Vite development server instead of the embedded frontend.
    //
    // `tauri_build::is_dev()` is true exactly when the `custom-protocol` feature
    // is off, i.e. when the webview will load `devUrl` (tauri.conf.json) rather
    // than the embedded `frontend/dist`. That is the intended behaviour for
    // `cargo build` / `tauri dev`, but it is a footgun for `cargo build --release`:
    // the resulting binary looks standalone yet cannot start without a running
    // Vite server, and fails at runtime with "backend unreachable".
    //
    // The supported standalone build is `npx tauri build --no-bundle`, which
    // enables `custom-protocol` and embeds the frontend. A release-profile build
    // that keeps the dev URL is only legitimate when deliberately opted into.
    //
    // Opt in deliberately with:  SYTHION_ALLOW_DEV_URL=1 cargo build --release
    let is_dev = tauri_build::is_dev();
    let profile = std::env::var("PROFILE").unwrap_or_default();
    let opt_in = std::env::var("SYTHION_ALLOW_DEV_URL").is_ok();

    if is_dev && profile == "release" && !opt_in {
        eprintln!();
        eprintln!("error: this release-profile build would produce an executable that");
        eprintln!("       depends on the Vite development server (devUrl), not the");
        eprintln!("       embedded frontend. It would not run standalone.");
        eprintln!();
        eprintln!("  For a standalone application, build with the Tauri CLI:");
        eprintln!("      npx tauri build --no-bundle");
        eprintln!();
        eprintln!("  To deliberately build a release-profile development shell, set:");
        eprintln!("      SYTHION_ALLOW_DEV_URL=1 cargo build --release");
        eprintln!();
        std::process::exit(1);
    }
}
