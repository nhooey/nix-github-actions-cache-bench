{
  description = "nix-github-actions-cache-bench dev-shell skill set — an isolated sub-flake invoked at RUNTIME by the root devShell, never a root input, so the skill sources live only in THIS flake's lock.";

  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";
    systems.url = "github:nix-systems/default";

    # The builder lib; it carries no skill inputs.
    agent-skill-flake = {
      url = "github:nhooey/agent-skill-flake";
      inputs.nixpkgs.follows = "nixpkgs";
    };

    # The Nix tooling family's skills: skillspkgs' `nix-tooling` combination.
    skillspkgs-combinations = {
      url = "github:nhooey/skillspkgs?dir=sources/combinations";
      inputs.nixpkgs.follows = "nixpkgs";
    };

    # Additions the rest of the Nix tooling family doesn't use. They follow
    # `nixpkgs` but not `agent-skill-flake`, so each keeps its own compatible
    # builder.
    nix-skills = {
      url = "github:nhooey/nix-skills";
      inputs.nixpkgs.follows = "nixpkgs";
    };
    python-skills = {
      url = "github:nhooey/python-skills";
      inputs.nixpkgs.follows = "nixpkgs";
    };
  };

  outputs =
    {
      nixpkgs,
      agent-skill-flake,
      ...
    }@inputs:
    agent-skill-flake.lib.mkDevshellSkillsFlake {
      inherit nixpkgs;
      systems = import inputs.systems;
      name = "nix-github-actions-cache-bench-devshell";
      sources = [
        { source = inputs.skillspkgs-combinations.combinations.nix-tooling; }
        # Addition: this repository is Python-first (scripts/ holds the
        # renderer, metrics extractor and scorer), unlike most of its family.
        {
          source = inputs.python-skills;
          skills = [ "python-hygiene-functional-style" ];
        }
        # Addition: the fixture flake builds babashka through clj-nix, a
        # Clojure toolchain no other Nix tooling repository uses.
        {
          source = inputs.nix-skills;
          skills = [ "nix-clojure" ];
        }
      ];
    };
}
