@echo off
:: Force the script to run from the folder it is saved in
cd /d "%~dp0"

:: Run the python script
python camera_relative_stick_pygame.py
pause