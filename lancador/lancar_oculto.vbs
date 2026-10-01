' Roda um script .sh no Git Bash sem janela e sem esperar (destacado).
' Uso: wscript.exe //B lancador\lancar_oculto.vbs "<projeto>/lancador/iniciar.sh"
Set sh = CreateObject("WScript.Shell")
cmd = """C:\Program Files\Git\bin\bash.exe"" """ & WScript.Arguments(0) & """"
sh.Run cmd, 0, False
