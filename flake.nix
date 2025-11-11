{
  description = "Hodor - AI-powered PR review tool";

  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";
    flake-utils.url = "github:numtide/flake-utils";
  };

  outputs = { self, nixpkgs, flake-utils }:
    flake-utils.lib.eachDefaultSystem (system:
      let
        pkgs = nixpkgs.legacyPackages.${system};
      in
      {
        devShells.default = pkgs.mkShell {
          buildInputs = with pkgs; [
            # Python and uv
            python313
            uv

            # System libraries needed for tokenizers and other compiled packages
            stdenv.cc.cc.lib
            gcc

            # Additional build dependencies
            pkg-config
            openssl
          ];

          # Set LD_LIBRARY_PATH to include necessary libraries
          shellHook = ''
            export LD_LIBRARY_PATH="${pkgs.stdenv.cc.cc.lib}/lib:$LD_LIBRARY_PATH"
            echo "NixOS development environment loaded"
            echo "You can now run: uv run hodor <url>"
          '';
        };
      }
    );
}
