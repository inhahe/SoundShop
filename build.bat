rmdir /s /q build
mkdir build
cd build
cmake .. -A x64 -DCMAKE_BUILD_TYPE=Release
cmake --build . --config Release --verbose
copy "bin\Release\JUCE GUI Server.exe" ..\juce_gui_server.exe
cd ..
rmdir /s /q build
