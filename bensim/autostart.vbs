' Runs bensim\autostart.ps1 with no window (used by the Windows task "bensim autostart").
CreateObject("Wscript.Shell").Run "powershell -NoProfile -ExecutionPolicy Bypass -File ""D:\Devops\trading\OpenTerminalUI\bensim\autostart.ps1""", 0, False
