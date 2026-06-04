Set sh = CreateObject("WScript.Shell")
sh.CurrentDirectory = "D:\Sector_Says_What\python"
sh.Run "cmd /c python sector_says_engine.py >> ..\engine.log 2>&1", 0, False
