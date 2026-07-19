if not exist build mkdir build
cd build
cmake .. -A x64 -DCMAKE_BUILD_TYPE=Release
cmake --build . --config Release --verbose
copy /y "bin\Release\juce_gui_server.exe" ..\
cd ..