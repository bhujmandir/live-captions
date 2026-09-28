@echo off
rem Live captions - the proper way to stop the server.
rem
rem Double-click this file. It asks the server to shut down cleanly: the
rem network advert is withdrawn, any reprocessing job is drained and the log
rem records a clean stop rather than a hole.
rem
rem WHY THIS EXISTS: pressing Ctrl+C in the server's console window used to end
rem the captions instantly, and twice - on the evening of 3 September - it did
rem exactly that in front of a full hall. Ctrl+C is now refused. See issue #61.
rem
rem The X-Captions-Control header is not decoration. Without it, any web page
rem open in a browser ON THIS MACHINE could auto-submit a form to the same URL
rem and stop the captions, because that browser is also 127.0.0.1. A form
rem cannot set a custom header; this script can.
rem
rem If the server was started on a port other than 8765, pass it:
rem     stop-captions.bat 9000

setlocal
set PORT=%~1
if "%PORT%"=="" set PORT=8765

echo Asking the captions server on port %PORT% to stop...

rem -f makes curl fail on 4xx/5xx. Without it a 403 exits 0 and this script
rem would cheerfully report success for a shutdown that never happened.
curl -f -s -S -X POST -H "X-Captions-Control: shutdown" "http://127.0.0.1:%PORT%/api/shutdown"
if errorlevel 1 (
  echo.
  echo FAILED. The server did not accept the request on port %PORT%.
  echo It may already be stopped, or it may be on a different port.
) else (
  echo.
  echo Sent. The server is shutting down.
  echo Its window stays open showing "Server stopped" until you press a key.
)

echo.
pause
