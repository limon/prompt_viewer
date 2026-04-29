{
  description = "Prompt Viewer development shell";

  inputs.nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";

  outputs = { nixpkgs, ... }:
    let
      systems = [
        "x86_64-linux"
        "aarch64-linux"
        "x86_64-darwin"
        "aarch64-darwin"
      ];
      forAllSystems = nixpkgs.lib.genAttrs systems;
    in
    {
      devShells = forAllSystems (system:
        let
          pkgs = import nixpkgs { inherit system; };
        in
        {
          default = pkgs.mkShell {
            packages = [
              pkgs.python313
            ];
            shellHook = ''
              if [ ! -d .venv ] || [ requirements.lock.txt -nt .venv/.requirements.lock.stamp ]; then
                python -m venv .venv
                .venv/bin/python -m pip install -r requirements.lock.txt
                cp requirements.lock.txt .venv/.requirements.lock.stamp
              fi
              export VIRTUAL_ENV="$PWD/.venv"
              export PATH="$VIRTUAL_ENV/bin:$PATH"
            '';
          };
        });
    };
}
