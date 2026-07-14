# Installer decisions

This is a human-readable extraction from `install.raw.log`. It records semantic
choices and notable prompt handling; it is not a replacement for the raw log.

| Stage | Prompt or action | Response or resolved value |
| --- | --- | --- |
| 9boot | Select serial console | `console=0` |
| 9boot | Start kernel | `boot` |
| Display | `vgasize` prompt | `text` |
| Shell | Start installer | `inst/start` |
| `configfs` | Filesystem | `hjfs` |
| `partdisk` | Target disk | `sd00` |
| `partdisk` | Partition table | `mbr` |
| `fdisk` | Proposed whole-disk Plan 9 partition | `w`, then `q` |
| `prepdisk` | Plan 9 partition | Default `/dev/sd00/plan9` |
| `disk/prep` | Proposed `9fat`, `nvram`, and `fs` layout | `w`, then `q` |
| `mountfs` | HJFS partition | Default `/dev/sd00/fs` |
| `mountfs` | RAM filesystem cache | Default `147` MiB |
| `mountfs` | Ream filesystem | Default `yes` |
| `confignet` | Ethernet configuration | Default `automatic` |
| `mountdist` | Distribution device | `/dev/sd01/data` |
| `mountdist` | Archive location | Default `/` |
| `ndbsetup` | System name | Default `cirno` |
| `tzsetup` | First pass | Default `US_Eastern` accepted unintentionally |
| Installer menu | Correct timezone | Rerun `tzsetup` |
| `tzsetup` | Final timezone | `US_Pacific` |
| `bootsetup` | FAT boot partition | Default `/dev/sd00/9fat` |
| `bootsetup` | Install Plan 9 MBR | `yes` |
| `bootsetup` | Mark Plan 9 partition active | `yes` |
| `finish` | Complete installation | Default `finish`; halt and reboot |

The installed `plan9.ini` observed on first boot contained:

```text
bootfile=9pc64
bootargs=local!/dev/sd00/fs -m 147
mouseport=ask
monitor=ask
vgasize=text
console=0
```
