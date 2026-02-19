' Запускає Main.py повністю у фоні — без вікна CMD
Dim objShell
Set objShell = CreateObject("WScript.Shell")

' Змінити шлях якщо python або папка інші
objShell.Run "python C:\Users\maksi\Desktop\manga\Main.py", 0, False

Set objShell = Nothing
