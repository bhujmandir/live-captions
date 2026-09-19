@echo off
rem Live captions - daily start.
rem
rem Double-click this file to start the captions server. Nothing to type.
rem Requires the one-time setup in WINDOWS.md to have been run first
rem (powershell -ExecutionPolicy Bypass -File deploy.ps1).
rem
rem This assumes the install already happened; it does not install or
rem update anything. For a fresh machine, run deploy.ps1 first.
rem
rem ---------------------------------------------------------------------
rem  WHY THIS WINDOW SHOUTS AT YOU
rem
rem  On 3 September the captions died mid-katha because this window
rem  appeared, said nothing, and the person who found it reasonably closed
rem  it. An unlabelled console window in a control room is an invitation
rem  to close it. So it now has a title and a banner, and the far better
rem  answer is not to have a window at all - see register-startup-task.ps1
rem  and WINDOWS.md, which run the server with no console.
rem
rem  TO STOP THE CAPTIONS: double-click stop-captions.bat.
rem ---------------------------------------------------------------------

title *** LIVE CAPTIONS RUNNING - DO NOT CLOSE THIS WINDOW ***

cd /d "%~dp0"

echo.
echo  ===================================================================
echo.
echo    L I V E   C A P T I O N S   -   R U N N I N G
echo.
echo    DO NOT CLOSE THIS WINDOW.
echo    Closing it takes the subtitles off the hall screen immediately.
echo.
echo    To stop the captions properly: double-click stop-captions.bat
echo.
echo    Operator page:  http://localhost:8765/
echo    Overlay (vMix): http://localhost:8765/?overlay=1
echo.
echo  ===================================================================
echo.

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0deploy.ps1" -StartOnly

rem If the server exited (a crash, stop-captions.bat, or three fast Ctrl+C
rem presses), keep the window open so an error message is readable instead
rem of the window vanishing instantly.
echo.
echo Server stopped. Press any key to close this window.
pause >nul
