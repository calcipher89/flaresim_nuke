# Building FlareSim+ for Linux (from a Windows PC)

This builds the Linux plugins on a Windows PC with WSL2 and Docker, so there's no need to dual boot. The Docker image uses the same Rocky Linux 8 toolchain Foundry uses for Nuke 15, so the plugins run on RHEL 8/9-type systems such as AlmaLinux 8 and 9.

The build machine needs internet access (it downloads the build image and CUDA). The studio machines don't.

## 1. Install WSL2 and Docker (once)

In an admin PowerShell:

```powershell
wsl --install -d Ubuntu-24.04
```

Restart when asked, then open **Ubuntu** from the Start menu and create a user.

Install [Docker Desktop](https://www.docker.com/products/docker-desktop/). In its settings, under **Resources → WSL integration**, turn on **Ubuntu-24.04**. In the Ubuntu terminal, `docker run --rm hello-world` should print a greeting.

## 2. Unpack the Linux Nuke (once per Nuke version)

The build needs the NDK headers and `libDDImage.so` from a Linux Nuke install of the **same version the studio runs** (e.g. 15.1v3). No licence is needed for this.

Download the Linux installer for that version from Foundry, then in the Ubuntu terminal:

```bash
cd ~/Downloads        # or wherever you saved it; Windows drives are under /mnt/c/...
tar xzf Nuke15.1v3-linux-x86_64.tgz
sudo ./Nuke15.1v3-linux-x86_64.run --prefix=/opt --accept-foundry-eula
ls /opt/Nuke15.1v3/include/DDImage/Iop.h    # should exist
```

## 3. Get the source

Clone inside the Linux filesystem (much faster than `/mnt/c`):

```bash
git clone https://github.com/calcipher89/flaresim_nuke.git ~/flaresim_nuke
cd ~/flaresim_nuke
```

To update later: `git pull`.

## 4. Build

```bash
scripts/build_docker.sh --images --versions "15"          # once: builds the image
scripts/build_docker.sh --versions "15" --nuke-root /opt  # every build
scripts/package_release.sh --version 1.0.0 --nuke-versions "15"
```

The result is `release_packages/FlareSim_v1.0.0_Nuke15_linux.zip`. It unpacks to a `FlareSim/` folder. Copy it to Windows with `cp release_packages/*.zip /mnt/c/Users/<you>/Desktop/`.

`--versions "15"` builds against the newest `/opt/Nuke15.*` it finds. For several Nuke versions, unpack each one into `/opt` and list them, e.g. `--versions "15 16"`. A build for 15.1 only loads in 15.1.

## 5. Check the build before it leaves the PC

```bash
cd dist/nuke15
objdump -T FlareSim.so | grep -o 'GLIBC_[0-9.]*' | sort -Vu | tail -1     # expect 2.28 or lower
objdump -T FlareSim.so | grep -o 'GLIBCXX_[0-9.]*' | sort -Vu | tail -1   # expect 3.4.25 or lower
```

Higher numbers mean the plugin needs a newer Linux than AlmaLinux 8 has.

## 6. At the studio

1. Unzip `FlareSim/` to the plugin share and add it in the facility `init.py`:
   ```python
   nuke.pluginAddPath('/path/to/FlareSim')
   ```
2. Put studio lenses in a shared folder and set `FLARESIM_LENS_PATH` to it (and `FLARESIM_LOOKS_PATH` for studio looks).
3. Run the self-test on a workstation, and once as a farm job on a GPU render node:
   ```bash
   nuke -t /path/to/FlareSim/FlareSim_SelfTest.py
   nuke -t /path/to/FlareSim/FlareSim_SelfTest.py --script /path/to/a/shot.nk
   ```
   Each run writes `FlareSim_selftest_<machine>_<date>.txt` to your home folder. Bring the reports back if anything shows `FAIL` or `WARN`.

Useful commands on a studio machine:

```bash
cat /etc/almalinux-release                                   # OS version
nvidia-smi --query-gpu=name,driver_version --format=csv      # GPU and driver (525 or newer)
```
