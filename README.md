<div align="center">

# OpenWRT - PakGrab

**Download OpenWrt packages the way OpenWrt does it, on any computer.**

Verified package lists, automatic dependency resolution and full repository mirroring, all from a familiar OpenWrt-style terminal or a clean desktop window.

![Python](https://img.shields.io/badge/python-3.10%2B-blue)
![Windows](https://img.shields.io/badge/Windows-0078D4)
![Linux](https://img.shields.io/badge/Linux-E95420)
![macOS](https://img.shields.io/badge/macOS-238636)

</div>

## Features

- **Works like OpenWrt.** The shell looks and feels like a router terminal.
- **Commands that match your firmware.** If your feeds file is for `apk`, you type `apk update`. If it is for `opkg`, you type `opkg update`. No new commands to learn.
- **Both package managers.** Supports `opkg` (`.ipk`) and the newer `apk` (`.apk`) releases.
- **Signature verification.** Package lists are checked against the public keys in your `keys` folder before anything is trusted.
- **Integrity checks.** Every downloaded package is verified against the hash in the signed index.
- **Dependencies included.** Ask for one package and get everything it needs. Use `-n` to skip dependencies.
- **Full mirror.** One command downloads every package of every feed, with a live progress bar showing percentage, count, speed and elapsed time.
- **Safe to re-run.** Files that are already up to date are skipped and outdated versions are replaced, so updating a mirror is fast.
- **Same layout as the server.** Files are saved in the same folder structure as the feed URLs, so a downloaded mirror is easy to browse and reuse.
- **Zero dependencies.** The core is pure Python standard library, Only the optional gui needs PySide6.

## Quick start

### Run from source

```
git clone https://github.com/fullband7/openwrt-pakgrab.git
cd openwrt-pakgrab
python pakgrab.py
```

For the GUI window install PySide6 once, then start it :

```
pip install PySide6-Essentials
python pakgrab_gui.py
```

The package command depends on your `distfeeds.conf` :

```
root@OpenWrt:~# apk update
root@OpenWrt:~# apk dl dnsmasq-full
```

or, for `opkg` releases :

```
root@OpenWrt:~# opkg update
root@OpenWrt:~# opkg dl dnsmasq-full
```

Downloaded files are saved in the `repo` folder next to the program.

### Download from a list

Put all the packages you want in a `.txt` file, separated by spaces or one per line, and pass the file to `dl` :

```
root@OpenWrt:~# apk dl mypkg.txt
root@OpenWrt:~# opkg dl mypkg.txt
```

Any file name works as long as it ends with `.txt`. Duplicates are ignored, shared dependencies are downloaded once, and packages that are not found are reported while the rest continue. Lines can have `#` notes. Use `-n` to skip dependencies.

### Windows App

Grab the ready-made `Standalone` or `Onefile` (exe) from the [Releases](https://github.com/fullband7/openwrt-pakgrab/releases) page.

## Commands

Pakgrab reads your `distfeeds` file and picks the matching command name automatically : `apk` for apk feeds and `opkg` for opkg feeds. The commands work the same way in both cases.

| Command | What it does |
| --- | --- |
| `apk update` / `opkg update` | Download the package lists of all feeds and verify their signatures |
| `apk dl <pkg>` / `opkg dl <pkg>` | Download a package together with its dependencies |
| `apk mirror all` / `opkg mirror all` | Download every package of every feed |
| `apk mirror --feeds base,luci` / `opkg mirror --feeds base,luci` | Mirror only the chosen feeds |
| `apk mirror --arch <arch>` / `opkg mirror --arch <arch>` | Mirror only one architecture |
| `apk mirror --packages a,b,list.txt` / `opkg mirror --packages a,b,list.txt` | Mirror only the listed packages |
| `set jobs 8` / `set limit-rate 2M` | Change parallel downloads or the speed limit |
| `feed r` | Reload the feed file after you edit it |
| `help` | Show the built-in help |
| `clear` | Clear the screen |
| `exit` | Close the session |

Type `help` inside Pakgrab to see the full list for your setup.

### Run a single command

Handy for scripts and scheduled updates. Use `opkg` instead of `apk` with opkg feeds :

```
python pakgrab.py -e "apk update"
python pakgrab.py -e "apk dl dnsmasq-full"
python pakgrab.py -e "apk mirror all"
```

### Options

| Option | Description |
| --- | --- |
| `-c`, `--config` | Use a specific distfeeds file |
| `-d`, `--dir` | Output folder (default : `repo` next to the program) |
| `-k`, `--keys` | Folder with public keys (default : `keys` next to the program) |
| `-e`, `--exec` | Run one command and exit (terminal only) |
| `--allow-http` | Allow plain `http` feeds (HTTPS is required by default) |
| `-w`, `--width` | Output width, `0` detects the terminal (terminal only) |
| `--timeout` | Network timeout in seconds (default : 30) |

## Configuration

Pakgrab needs two things next to the program : your feeds file and your trusted keys.

```
openwrt-pakgrab/
├── pakgrab.py        terminal
├── pakgrab_gui.py    desktop window
├── pakgrab/          core, no dependencies
├── pakgrab_gui/      window, needs PySide6
├── distfeeds.list    (or distfeeds.conf)
├── keys/
└── repo/             created automatically
```

### Feeds file

Place your own `distfeeds` file next to Pakgrab. Both `distfeeds.conf` and `distfeeds.list` are supported, so no renaming or conversion is needed. The package manager and architecture are detected from it automatically.

### Keys

Put OpenWrt's public keys in the `keys` folder :

- usign key files for `opkg` releases
- `.pem` files for `apk` releases

You can copy them from any OpenWrt device of the same release (`/etc/opkg/keys` or `/etc/apk/keys`). If no keys are found, Pakgrab warns you at startup.

## Using the offline repository

After the mirror is built, you can serve it to your routers from any computer on your network.

### 1. Host the `repo` folder

Use any HTTP/HTTPS server you like and host the `repo` folder, so that it is reachable at `http://<server-ip>/repo/`.

### 2. Point the router to your server

On the router, edit the feeds file and replace the official address with your server address.

| Package manager | File on the router |
| --- | --- |
| `apk` | `/etc/apk/repositories.d/distfeeds.list` |
| `opkg` | `/etc/opkg/distfeeds.conf` |

For example, this line :

```
https://downloads.openwrt.org/releases/25.12.0/packages/aarch64_cortex-a53/base/packages.adb
```

becomes :

```
http://192.168.1.10/repo/releases/25.12.0/packages/aarch64_cortex-a53/base/packages.adb
```

Only the beginning of each line changes : `https://downloads.openwrt.org` is replaced with `http://192.168.1.10/repo`. If your server uses a port other than 80, add it after the address, for example `http://192.168.1.10:8080/repo`. To do it in one command :

```
sed -i 's|https://downloads.openwrt.org|http://192.168.1.10/repo|g' /etc/apk/repositories.d/distfeeds.list
```

For `opkg`, use `/etc/opkg/distfeeds.conf` instead.

### 3. Install packages

```
apk update
apk add dnsmasq-full
```

or, for `opkg` :

```
opkg update
opkg install dnsmasq-full
```

The mirrored files are not modified, so the router keeps verifying signatures with its own keys.

## Disclaimer

Pakgrab is an independent project. It is not affiliated with, endorsed by or sponsored by the OpenWrt project.
