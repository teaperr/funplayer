{
  description = "FunPlayer - Linux funscript player with Intiface/Buttplug sync";

  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";
    flake-utils.url = "github:numtide/flake-utils";
  };

  outputs =
    {
      self,
      nixpkgs,
      flake-utils,
    }:
    flake-utils.lib.eachDefaultSystem (
      system:
      let
        pkgs = import nixpkgs { inherit system; };
        py = pkgs.python313;
        pythonMpv = py.pkgs.mpv;

        funplayer = py.pkgs.buildPythonApplication {
          pname = "funplayer";
          version = "0.1.0";
          format = "pyproject";
          src = ./.;

          nativeBuildInputs = [
            py.pkgs.setuptools
            py.pkgs.wheel
            pkgs.qt6.wrapQtAppsHook
            pkgs.qt6.qtbase
          ];
          propagatedBuildInputs = [
            py.pkgs.pyside6
            py.pkgs.websockets
            pythonMpv
          ];
          pythonRemoveDeps = [ "python-mpv" ];

          # python-mpv loads libmpv via ctypes, so it needs to find it at runtime
          makeWrapperArgs = [
            "--prefix LD_LIBRARY_PATH : ${pkgs.mpv-unwrapped}/lib"
          ];

          dontWrapQtApps = false;
        };
      in
      {
        packages.default = funplayer;
        apps.default = {
          type = "app";
          program = "${funplayer}/bin/funplayer";
        };
        devShells.default = pkgs.mkShell {
          buildInputs = [
            (py.withPackages (ps: [
              ps.pyside6
              ps.websockets
              pythonMpv
            ]))
            pkgs.mpv-unwrapped
          ];
        };
      }
    )
    // {
      homeManagerModules.default =
        {
          config,
          lib,
          pkgs,
          ...
        }:
        let
          cfg = config.programs.funplayer;
          system = pkgs.system;
        in
        {
          options.programs.funplayer = {
            enable = lib.mkEnableOption "FunPlayer funscript player";
          };
          config = lib.mkIf cfg.enable {
            home.packages = [ self.packages.${system}.default ];
          };
        };
    };
}
