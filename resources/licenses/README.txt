MusicDesk is free software under the GNU General Public License, version 3 or later (GPL-3.0-or-later); its
licence text is GPL-3.0.txt.

MusicDesk ships with these third-party components. Their licence texts are in this folder.

  Python                          PSF License 2.0       python-PSF.txt
  Qt for Python (PySide6,         LGPL-3.0              LGPL-3.0.txt, and GPL-3.0.txt, which the
    shiboken6)                                          LGPL-3.0 supplements
  mutagen                         GPL-2.0-or-later      mutagen-GPL-2.0.txt
  watchdog                        Apache-2.0            watchdog-Apache-2.0.txt, watchdog-NOTICE.txt
  natsort                         MIT                   natsort-MIT.txt
  Send2Trash                      BSD-3-Clause          send2trash-BSD.txt
  rsgain 3.6                      BSD-2-Clause          rsgain-BSD-2-Clause.txt
  CRC++ (built into rsgain)       BSD-3-Clause          CRCpp-BSD.txt

  FFmpeg 7.1 (built into rsgain)  LGPL-2.1-or-later     FFmpeg-LGPL-2.1.txt
  Fraunhofer FDK AAC (built into  FDK AAC licence       FDK-AAC-NOTICE.txt
    rsgain)

rsgain 3.6's Windows build gets FFmpeg from vcpkg with the avcodec, avformat, swresample and fdk-aac features,
and without "gpl" (see rsgain's vcpkg.json at https://github.com/complexlogic/rsgain), so FFmpeg is under the
LGPL. Its source code is at https://ffmpeg.org/, and rsgain's at https://github.com/complexlogic/rsgain.
The FDK AAC library's source code is at https://github.com/mstorsjo/fdk-aac.
