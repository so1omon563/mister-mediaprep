#!/bin/bash
export PYTHONUTF8=1 PYTHONDONTWRITEBYTECODE=1
exec python3 -B /media/fat/Scripts/.config/mister-mediaprep/mister-mediaprep.pyz "$@"
