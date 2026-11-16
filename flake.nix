{
  description = "Benchmark of free GitHub Actions Nix caches: the dev shell for rendering, running and scoring the bench. The fixture builds live in the separate fixtures/ flake.";

  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";
    flake-parts = {
      url = "github:hercules-ci/flake-parts";
      inputs.nixpkgs-lib.follows = "nixpkgs";
    };
    systems.url = "github:nix-systems/default";
    treefmt-nix = {
      url = "github:numtide/treefmt-nix";
      inputs.nixpkgs.follows = "nixpkgs";
    };
    # The builder library, not a skill: it exports the
    # `flakeModules.devshellSkills` module for the dev shell. The dev shell's
    # skills live in the `skills-devshell/` sub-flake, invoked at runtime, so
    # this flake carries no skill inputs.
    agent-skill-flake = {
      url = "github:nhooey/agent-skill-flake";
      inputs.nixpkgs.follows = "nixpkgs";
    };
  };

  outputs =
    { flake-parts, ... }@inputs:
    flake-parts.lib.mkFlake { inherit inputs; } {
      systems = import inputs.systems;
      imports = [
        inputs.agent-skill-flake.flakeModules.devshellSkills
        inputs.treefmt-nix.flakeModule
      ];

      agent-skill-flake.devshellSkills.name = "nix-github-actions-cache-bench";

      perSystem =
        { pkgs, ... }:
        {
          # What scripts/ runs: the Python scripts and the gh CLI.
          devshells.default.packages = [
            pkgs.gh
            pkgs.python3
          ];

          # `nix fmt` formats, and `nix flake check` fails on unformatted
          # files. The workflow templates carry `@...@` placeholders and
          # `#@section` markers no YAML formatter keeps, and results/ is data.
          treefmt = {
            projectRootFile = "flake.nix";
            programs.nixfmt.enable = true;
            programs.ruff-format.enable = true;
            programs.shfmt.enable = true;
            settings.global.excludes = [
              "workflows/*"
              "results/*"
            ];
          };
        };
    };
}
