Refrain is free software under the GNU General Public License, version 3 or later (GPL-3.0-or-later); its
licence text is GPL-3.0.txt.

Refrain ships with these third-party components. Their licence texts are in this folder.

  The app itself
  .NET runtime 10.0.14            MIT                   dotnet-MIT.txt, dotnet-THIRD-PARTY-NOTICES.txt
    (Native AOT, built into
    Refrain.exe)
  Windows App SDK 2.6.4           Microsoft Software    WindowsAppSDK-license.txt, WindowsAppSDK-NOTICE.txt,
    (WinUI 3)                     Licence Terms         WinUI-NOTICE.txt
  WebView2 loader 1.0.3719.77     BSD-3-Clause          WebView2-BSD.txt
    (with the Windows App SDK)
  TagLib# 2.3.0                   LGPL-2.1              LGPL-2.1.txt
  Microsoft.Data.Sqlite 10.0.14   MIT                   dotnet-MIT.txt
  SQLitePCLRaw 2.1.13             Apache-2.0            SQLitePCLRaw-Apache-2.0.txt
  SQLite 3.53.3 (in SQLitePCLRaw) Public domain         SQLite-public-domain.txt

  The bundled tools
  rsgain 3.6                      BSD-2-Clause          rsgain-BSD-2-Clause.txt
  CRC++ (built into rsgain)       BSD-3-Clause          CRCpp-BSD.txt
  FFmpeg 7.1 (built into rsgain)  LGPL-2.1-or-later     LGPL-2.1.txt
  Fraunhofer FDK AAC (built into  FDK AAC licence       FDK-AAC-NOTICE.txt
    rsgain)
  flac 1.5.0                      GPL-2.0-or-later      flac-GPL-2.0.txt
  libFLAC 1.5.0 (with flac)       BSD-3-Clause          libFLAC-Xiph-BSD.txt
                                  (Xiph.Org)

The texts come from the components' own packages: the .NET and Windows App SDK files as their NuGet packages ship
them; Microsoft.Data.Sqlite is under the same MIT licence as .NET (.NET Foundation and Contributors); the Apache
licence text is the one .NET ships in its notices. TagLib# is compiled into Refrain.exe; its source code is at
https://github.com/mono/taglib-sharp, and Refrain's, to rebuild it with a changed TagLib#, at
https://github.com/hugo-cacciatori/refrain.

rsgain 3.6's Windows build gets FFmpeg from vcpkg with the avcodec, avformat, swresample and fdk-aac features,
and without "gpl" (see rsgain's vcpkg.json at https://github.com/complexlogic/rsgain), so FFmpeg is under the
LGPL. Its source code is at https://ffmpeg.org/, and rsgain's at https://github.com/complexlogic/rsgain.
The FDK AAC library's source code is at https://github.com/mstorsjo/fdk-aac.

flac 1.5.0 is the official Xiph.Org Win64 build (flac-1.5.0-win.zip from https://github.com/xiph/flac/releases);
Refrain runs flac.exe to cut single-file albums into tracks. Its source code is at https://github.com/xiph/flac.
