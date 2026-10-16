# ES-DE for Prism

Prism runs ES-DE 3.5.0 with a small patch (`prism-es-de.patch`, against the commit in `BASE`):

- gamelists are written atomically (a full disk or a crash can no longer leave an empty gamelist)
- one ambient home background keeps playing while you move between system tiles
- the front-page collections and folder cards Prism's theme uses
- a favorite "burst" animation

ES-DE is MIT-licensed (`LICENSE-es-de`).

## Building it

The build runs in an Ubuntu 22.04 container so the result works with the libraries in ES-DE's own AppImage.

```bash
distrobox create --name esde-build --image docker.io/library/ubuntu:22.04
distrobox enter esde-build -- sudo apt-get install -y build-essential git cmake gettext libharfbuzz-dev \
  libicu-dev libsdl2-dev libavcodec-dev libavfilter-dev libavformat-dev libavutil-dev libfreeimage-dev \
  libfreetype6-dev libgit2-dev libcurl4-openssl-dev libpugixml-dev libasound2-dev libbluetooth-dev \
  libgl1-mesa-dev libpoppler-cpp-dev patchelf

git clone https://gitlab.com/es-de/emulationstation-de.git src
cd src && git checkout "$(cut -d' ' -f1 ../BASE)" && git apply ../prism-es-de.patch
distrobox enter esde-build -- sh -c 'cmake -DCMAKE_BUILD_TYPE=Release -DAPPIMAGE_BUILD=on -DBUNDLED_CERTS=on \
  -DSTEAM_DECK=on . && make -j6'
distrobox enter esde-build -- patchelf --set-rpath '$ORIGIN/../lib' es-de
```

Then extract the official ES-DE 3.5.0 AppImage (`./ES-DE_x64.AppImage --appimage-extract`) and replace
`squashfs-root/usr/bin/es-de` with the new binary (the installer does this). Steam's "Prism" shortcut runs
`~/.local/share/prism/prism.sh`, which starts that binary with Prism's own ES-DE folder.

## The other files here

| File | What it is |
|---|---|
| `scripts/<event>/*.sh` | ES-DE event hooks (game start / end, selection, screensaver, startup). ES-DE runs every file in these folders, so never leave backup copies in them |
| `custom_systems/es_systems.xml` | Prism's extra systems (Steam, Movies, TV, Music, Xbox 360 through Xenia ...) |

`@HOME@`, `@PRISM_DIR@`, `@ROMS@`, `@EMULATION@` and `@ESDE@` are filled in by the installer.
