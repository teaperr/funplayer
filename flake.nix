{
  description = "FunPlayer - Linux funscript player with Intiface/Buttplug sync";

  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";
    flake-utils.url = "github:numtide/flake-utils";
  };

  outputs = { self, nixpkgs, flake-utils }:
    flake-utils.lib.eachDefaultSystem (system:
      let
        pkgs = import nixpkgs { inherit system; };
        py = pkgs.python313;

        # nixpkgs' python3Packages.mpv is the python-mpv bindings package.
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

          # pyproject.toml declares "python-mpv" as a dependency, but the
          # actual package providing that import is nixpkgs' python3Packages.mpv
          # (different dist-info name) -- strip the declared name from the
          # runtime-deps check rather than fight the mismatch; the real
          # module is still supplied via propagatedBuildInputs above.
          pythonRemoveDeps = [ "python-mpv" ];

          # python-mpv loads libmpv via ctypes, so it needs to find it at runtime
          makeWrapperArgs = [
            "--prefix LD_LIBRARY_PATH : ${pkgs.mpv-unwrapped}/lib"
            # ffprobe, used to show video lengths in the playlist
            "--prefix PATH : ${pkgs.ffmpeg-headless}/bin"
          ];

          dontWrapQtApps = false;
        };

        funplayerDesktopItem = pkgs.makeDesktopItem {
          name = "funplayer";
          desktopName = "FunPlayer";
          comment = "Funscript player with Intiface/Buttplug sync";
          exec = "${funplayer}/bin/funplayer %f";
          # Swap for a real icon file if/when you add one to the repo, e.g.:
          # icon = ./assets/funplayer.png;
          icon = "multimedia-player";
          categories = [ "AudioVideo" "Player" ];
          mimeTypes = [ "video/mp4" "video/x-matroska" "audio/mpeg" ];
        };

        funplayerWithDesktop = pkgs.symlinkJoin {
          name = "funplayer-with-desktop";
          paths = [ funplayer funplayerDesktopItem ];
        };
      in
      {
        packages.default = funplayerWithDesktop;
        packages.funplayer = funplayer;  # plain binary, no desktop entry, if you ever want it standalone
        apps.default = {
          type = "app";
          program = "${funplayer}/bin/funplayer";
        };
        devShells.default = pkgs.mkShell {
          buildInputs = [
            (py.withPackages (ps: [ ps.pyside6 ps.websockets pythonMpv ]))
            pkgs.mpv-unwrapped
            pkgs.ffmpeg-headless
          ];
        };
      }
    ) // {
      homeManagerModules.default = { config, lib, pkgs, ... }:
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
