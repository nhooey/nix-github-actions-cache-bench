{
  description = "Workloads for nix-github-actions-cache-bench";

  inputs = {
    # clj-nix at the commit round 1 (2026-10-13) tested: the fork's
    # integration branch, with babashka 1.13.226 and the features fix.
    # Pinned by SHA, so the integration rebuild can't move it.
    clj-nix.url = "github:nhooey/clj-nix/641813992fc6b19ee1ea5c356a8ac8ca46b10937";
    # clj-nix's locked nixpkgs (6510408, 2026-10-10), as in round 1's pin
    nixpkgs.follows = "clj-nix/nixpkgs";

    crane.url = "github:ipetkov/crane/v0.24.0";

    ripgrep-src = {
      url = "github:BurntSushi/ripgrep/15.2.0";
      flake = false;
    };
  };

  outputs = { self, nixpkgs, clj-nix, crane, ripgrep-src }:
    let
      inherit (nixpkgs) lib;

      supportedSystems = [ "x86_64-linux" "aarch64-linux" "aarch64-darwin" ];

      # scripts/salt.sh writes this file and commits it. Empty means
      # "unsalted": the derivations are then the same as round 1's.
      salt = lib.removeSuffix "\n" (builtins.readFile ./salt);
      salted = salt != "";

      knobSets = import ./knobs.nix;

      perSystem = system:
        let
          pkgs = nixpkgs.legacyPackages.${system};

          # --- 1. babashka via clj-nix ---------------------------------
          # Unsalted: clj-nix's own package set, so babashka-test is the
          # same .drv as clj-nix#babashka and as round 1's e2e test.
          # Salted: change clj-builder's derivation, as a commit to
          # clj-nix's src/ would. Every mkCljBin output rebuilds, babashka
          # included; the Maven jars (fixed-output) stay the same.
          cljPkgs =
            if !salted then clj-nix.legacyPackages.${system}
            else
              import nixpkgs {
                inherit system;
                overlays = [
                  clj-nix.overlays.default
                  (final: prev: {
                    # overrideAttrs, not override: mkCljBin calls
                    # clj-builder.override itself, which would drop an
                    # override of preBuild, but keeps this attribute
                    clj-builder = prev.clj-builder.overrideAttrs {
                      BENCH_SALT = salt;
                    };
                  })
                ];
              };

          # --- 2. ripgrep via crane ------------------------------------
          craneLib = crane.mkLib pkgs;
          rgCommon = {
            pname = "ripgrep";
            version = "15.2.0";
            src = craneLib.cleanCargoSource ripgrep-src;
            strictDeps = true;
          };
          # Dependencies only: the vendored crates plus their compiled
          # artifacts. Not salted, like a real change to ripgrep's own code.
          ripgrep-deps = craneLib.buildDepsOnly rgCommon;
          ripgrep = craneLib.buildPackage (rgCommon // {
            cargoArtifacts = ripgrep-deps;
            # The bench measures the build, not ripgrep's test suite
            doCheck = false;
          } // lib.optionalAttrs salted { BENCH_SALT = salt; });

          # --- 3. synthetic knobs --------------------------------------
          # n derivations, each sleeping `sleep` seconds and writing `size`
          # random (incompressible) bytes. Each takes the salt, so a salt
          # rebuilds all of them. A linkFarm joins them into one attribute.
          mkKnob = name: { n, size, sleep }:
            let
              one = i: pkgs.runCommand "knob-${name}-${toString i}"
                {
                  inherit size sleep;
                  BENCH_SALT = salt;
                } ''
                sleep "$sleep"
                mkdir -p "$out"
                head -c "$size" /dev/urandom > "$out/blob"
              '';
            in
            pkgs.linkFarm "knob-${name}"
              (map (i: { name = "p${toString i}"; path = one i; }) (lib.range 1 n));
        in
        {
          babashka-test = cljPkgs.mkBabashka { };
          babashka-with-features-test = cljPkgs.mkBabashka {
            withFeatures = [ "jdbc" "sqlite" ];
          };
          inherit ripgrep ripgrep-deps;
        }
        // lib.mapAttrs' (name: knob: lib.nameValuePair "knob-${name}" (mkKnob name knob)) knobSets;
    in
    {
      packages = lib.genAttrs supportedSystems perSystem;

      # Workload name -> the attributes it builds, in order. The workflow
      # reads this with `nix eval --json .#workloads.<name>`.
      workloads = {
        babashka = [ "babashka-test" "babashka-with-features-test" ];
        ripgrep = [ "ripgrep" ];
      }
      // lib.mapAttrs' (name: _: lib.nameValuePair "knob-${name}" [ "knob-${name}" ]) knobSets;

      # For checks: the salt this tree evaluates with
      inherit salt;
    };
}
