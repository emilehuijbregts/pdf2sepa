; PDF2SEPA client installer with optional pre-seeded data (Inno Setup 6)
;
; Manual seed workflow:
;   1. Prepare seed\settings.json and seed\suppliers.json (Windows paths, no Mac paths)
;   2. Place them in a folder, e.g. C:\build\seed\
;   3. Compile:
;        iscc packaging\installer-client.iss /DMyAppVersion=1.0.1 /DSEEDDIR=C:\build\seed
;
; Seed files are copied only when settings.json does not yet exist (fresh install).

#include "installer.iss"

#ifndef SEEDDIR
  #error SEEDDIR must be defined, e.g. /DSEEDDIR=C:\build\seed
#endif

[Files]
Source: "{#SEEDDIR}\settings.json"; DestDir: "{localappdata}\PDF2SEPA\data"; Flags: onlyifdoesntexist
Source: "{#SEEDDIR}\suppliers.json"; DestDir: "{localappdata}\PDF2SEPA\data"; Flags: onlyifdoesntexist
