fn main() {
    // Register our custom build identity with rustc's check-cfg diagnostics.
    // Both production (unset) and prototype (set) builds are valid.
    println!("cargo:rustc-check-cfg=cfg(prototype_build)");
    tauri_build::build();

    // Build-specific prototype identity. The Tauri CLI communicates an alternate
    // configuration (including `--config`) through the `TAURI_CONFIG` environment
    // variable while this build script runs. Inspect that patch at compile time
    // so a prototype build is identifiable even if a macro or merge edge case
    // ever drops a field. Cargo-only builds (no `TAURI_CONFIG`) remain
    // production builds.
    let mut prototype = std::env::var("SYTHION_PROTECT_BUILD").ok().map(|v| {
        let v = v.to_lowercase();
        v == "1" || v == "true" || v == "yes"
    }).unwrap_or(false);

    if let Ok(raw) = std::env::var("TAURI_CONFIG") {
        // The merged config is a JSON patch. Only exact prototype markers move
        // this build onto the isolated profile; anything else stays production.
        if raw.contains("Sythion (Synth) Prototype")
            || raw.contains("ai.sythion.desktop.prototype")
        {
            prototype = true;
        }
    }

    if prototype {
        println!("cargo:rustc-cfg=prototype_build");
        println!("cargo:rustc-env=SYTHION_PROTECT_PROTECT=1");
    }

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
