// python_bindings.cpp - Python extension for communicating with juce_gui_server.exe
#include <Python.h>
#include <string>
#include <sstream>
#include <thread>
#include <chrono>

#ifdef _WIN32
#include <windows.h>
#else
#include <unistd.h>
#include <sys/types.h>
#include <sys/wait.h>
#include <fcntl.h>
#include <sys/stat.h>
#endif

class JuceAudioHost {
private:
    std::string pipeName;
#ifdef _WIN32
    HANDLE pipeHandle;
    HANDLE guiProcessHandle;
    PROCESS_INFORMATION guiProcessInfo;
#else
    int pipeHandle;
    pid_t guiProcessPid;
#endif
    bool connected;
    std::string guiServerPath;

public:
    JuceAudioHost() : connected(false), pipeHandle(nullptr), guiProcessHandle(nullptr) {
        // Generate unique pipe name
        pipeName = "juce_audio_pipe_" + std::to_string(GetCurrentProcessId());
        guiServerPath = "juce_gui_server.exe";  // Default path
    }

    ~JuceAudioHost() {
        disconnect();
    }

    void setGuiServerPath(const std::string& path) {
        guiServerPath = path;
    }

    bool connect() {
        if (connected) return true;

        // Launch the GUI server process
        if (!launchGuiServer()) {
            return false;
        }

        // Give the server time to start up
        std::this_thread::sleep_for(std::chrono::milliseconds(500));

        // Connect to the named pipe
        return connectToPipe();
    }

    void disconnect() {
        if (connected) {
#ifdef _WIN32
            // Send shutdown command
            sendCommand("shutdown:");
            
            // Close pipe
            if (pipeHandle && pipeHandle != INVALID_HANDLE_VALUE) {
                CloseHandle(pipeHandle);
                pipeHandle = nullptr;
            }

            // Terminate GUI process
            if (guiProcessHandle && guiProcessHandle != INVALID_HANDLE_VALUE) {
                // Wait for graceful shutdown
                WaitForSingleObject(guiProcessHandle, 5000);
                TerminateProcess(guiProcessHandle, 0);
                CloseHandle(guiProcessHandle);
                CloseHandle(guiProcessInfo.hThread);
                guiProcessHandle = nullptr;
            }
#else
            // Unix implementation
            sendCommand("shutdown:");
            
            if (pipeHandle != -1) {
                close(pipeHandle);
                pipeHandle = -1;
            }

            if (guiProcessPid > 0) {
                kill(guiProcessPid, SIGTERM);
                waitpid(guiProcessPid, nullptr, 0);
                guiProcessPid = 0;
            }
#endif
            connected = false;
        }
    }

    std::string sendCommand(const std::string& command) {
        if (!connected && command != "shutdown:") {
            if (!connect()) {
                return "ERROR:Not connected";
            }
        }

#ifdef _WIN32
        // Write command to pipe
        DWORD bytesWritten;
        if (!WriteFile(pipeHandle, command.c_str(), command.length(), &bytesWritten, nullptr)) {
            return "ERROR:Failed to write to pipe";
        }

        // Read response
        char buffer[4096];
        DWORD bytesRead;
        if (!ReadFile(pipeHandle, buffer, sizeof(buffer) - 1, &bytesRead, nullptr)) {
            return "ERROR:Failed to read from pipe";
        }
        buffer[bytesRead] = '\0';
        return std::string(buffer);
#else
        // Unix implementation
        write(pipeHandle, command.c_str(), command.length());
        
        char buffer[4096];
        ssize_t bytesRead = read(pipeHandle, buffer, sizeof(buffer) - 1);
        if (bytesRead > 0) {
            buffer[bytesRead] = '\0';
            return std::string(buffer);
        }
        return "ERROR:Failed to read from pipe";
#endif
    }

private:
    bool launchGuiServer() {
#ifdef _WIN32
        // Create process
        STARTUPINFOA si;
        ZeroMemory(&si, sizeof(si));
        si.cb = sizeof(si);
        ZeroMemory(&guiProcessInfo, sizeof(guiProcessInfo));

        // Command line with pipe name
        std::string cmdLine = guiServerPath + " " + pipeName;

        if (!CreateProcessA(
            nullptr,
            const_cast<char*>(cmdLine.c_str()),
            nullptr,
            nullptr,
            FALSE,
            0,
            nullptr,
            nullptr,
            &si,
            &guiProcessInfo
        )) {
            return false;
        }

        guiProcessHandle = guiProcessInfo.hProcess;
        return true;
#else
        // Unix fork/exec
        guiProcessPid = fork();
        if (guiProcessPid == 0) {
            // Child process
            execl(guiServerPath.c_str(), guiServerPath.c_str(), pipeName.c_str(), nullptr);
            exit(1);  // exec failed
        }
        return guiProcessPid > 0;
#endif
    }

    bool connectToPipe() {
#ifdef _WIN32
        std::string fullPipeName = "\\\\.\\pipe\\" + pipeName;
        
        // Try to connect to the pipe (with retries)
        for (int i = 0; i < 10; i++) {
            pipeHandle = CreateFileA(
                fullPipeName.c_str(),
                GENERIC_READ | GENERIC_WRITE,
                0,
                nullptr,
                OPEN_EXISTING,
                0,
                nullptr
            );

            if (pipeHandle != INVALID_HANDLE_VALUE) {
                connected = true;
                return true;
            }

            // Wait and retry
            std::this_thread::sleep_for(std::chrono::milliseconds(500));
        }
        return false;
#else
        // Unix named pipe
        std::string fullPipeName = "/tmp/" + pipeName;
        
        for (int i = 0; i < 10; i++) {
            pipeHandle = open(fullPipeName.c_str(), O_RDWR);
            if (pipeHandle != -1) {
                connected = true;
                return true;
            }
            std::this_thread::sleep_for(std::chrono::milliseconds(500));
        }
        return false;
#endif
    }
};

// Python wrapper class
typedef struct {
    PyObject_HEAD
    JuceAudioHost* host;
} PyJuceAudioHost;

static void PyJuceAudioHost_dealloc(PyJuceAudioHost* self) {
    delete self->host;
    Py_TYPE(self)->tp_free((PyObject*)self);
}

static PyObject* PyJuceAudioHost_new(PyTypeObject* type, PyObject* args, PyObject* kwds) {
    PyJuceAudioHost* self;
    self = (PyJuceAudioHost*)type->tp_alloc(type, 0);
    if (self != nullptr) {
        self->host = new JuceAudioHost();
    }
    return (PyObject*)self;
}

static int PyJuceAudioHost_init(PyJuceAudioHost* self, PyObject* args, PyObject* kwds) {
    const char* gui_server_path = nullptr;
    static char* kwlist[] = {"gui_server_path", nullptr};
    
    if (!PyArg_ParseTupleAndKeywords(args, kwds, "|s", kwlist, &gui_server_path)) {
        return -1;
    }
    
    if (gui_server_path) {
        self->host->setGuiServerPath(gui_server_path);
    }
    
    return 0;
}

// Python methods
static PyObject* PyJuceAudioHost_connect(PyJuceAudioHost* self) {
    if (self->host->connect()) {
        Py_RETURN_TRUE;
    }
    Py_RETURN_FALSE;
}

static PyObject* PyJuceAudioHost_disconnect(PyJuceAudioHost* self) {
    self->host->disconnect();
    Py_RETURN_NONE;
}

static PyObject* PyJuceAudioHost_load_plugin(PyJuceAudioHost* self, PyObject* args) {
    const char* path;
    const char* plugin_id;
    
    if (!PyArg_ParseTuple(args, "ss", &path, &plugin_id)) {
        return nullptr;
    }
    
    std::string command = "load_plugin:path=" + std::string(path) + ",id=" + std::string(plugin_id);
    std::string response = self->host->sendCommand(command);
    
    return PyUnicode_FromString(response.c_str());
}

static PyObject* PyJuceAudioHost_show_plugin_ui(PyJuceAudioHost* self, PyObject* args) {
    const char* plugin_id;
    
    if (!PyArg_ParseTuple(args, "s", &plugin_id)) {
        return nullptr;
    }
    
    std::string command = "show_plugin_ui:id=" + std::string(plugin_id);
    std::string response = self->host->sendCommand(command);
    
    return PyUnicode_FromString(response.c_str());
}

static PyObject* PyJuceAudioHost_hide_plugin_ui(PyJuceAudioHost* self, PyObject* args) {
    const char* plugin_id;
    
    if (!PyArg_ParseTuple(args, "s", &plugin_id)) {
        return nullptr;
    }
    
    std::string command = "hide_plugin_ui:id=" + std::string(plugin_id);
    std::string response = self->host->sendCommand(command);
    
    return PyUnicode_FromString(response.c_str());
}

static PyObject* PyJuceAudioHost_set_parameter(PyJuceAudioHost* self, PyObject* args) {
    const char* plugin_id;
    int param_index;
    float value;
    
    if (!PyArg_ParseTuple(args, "sif", &plugin_id, &param_index, &value)) {
        return nullptr;
    }
    
    std::stringstream ss;
    ss << "set_parameter:id=" << plugin_id 
       << ",param_index=" << param_index 
       << ",value=" << value;
    
    std::string response = self->host->sendCommand(ss.str());
    return PyUnicode_FromString(response.c_str());
}

static PyObject* PyJuceAudioHost_get_parameter(PyJuceAudioHost* self, PyObject* args) {
    const char* plugin_id;
    int param_index;
    
    if (!PyArg_ParseTuple(args, "si", &plugin_id, &param_index)) {
        return nullptr;
    }
    
    std::stringstream ss;
    ss << "get_parameter:id=" << plugin_id 
       << ",param_index=" << param_index;
    
    std::string response = self->host->sendCommand(ss.str());
    
    // Parse the response to extract the value
    if (response.find("OK:") == 0) {
        size_t valuePos = response.find("value=");
        if (valuePos != std::string::npos) {
            float value = std::stof(response.substr(valuePos + 6));
            return PyFloat_FromDouble(value);
        }
    }
    
    return PyFloat_FromDouble(0.0);
}

static PyObject* PyJuceAudioHost_connect_audio(PyJuceAudioHost* self, PyObject* args) {
    const char* source_id;
    int source_channel;
    const char* dest_id;
    int dest_channel;
    
    if (!PyArg_ParseTuple(args, "sisi", &source_id, &source_channel, &dest_id, &dest_channel)) {
        return nullptr;
    }
    
    std::stringstream ss;
    ss << "connect_audio:source_id=" << source_id 
       << ",source_channel=" << source_channel
       << ",dest_id=" << dest_id
       << ",dest_channel=" << dest_channel;
    
    std::string response = self->host->sendCommand(ss.str());
    return PyUnicode_FromString(response.c_str());
}

static PyObject* PyJuceAudioHost_connect_midi(PyJuceAudioHost* self, PyObject* args) {
    const char* source_id;
    const char* dest_id;
    
    if (!PyArg_ParseTuple(args, "ss", &source_id, &dest_id)) {
        return nullptr;
    }
    
    std::stringstream ss;
    ss << "connect_midi:source_id=" << source_id 
       << ",dest_id=" << dest_id;
    
    std::string response = self->host->sendCommand(ss.str());
    return PyUnicode_FromString(response.c_str());
}

static PyObject* PyJuceAudioHost_start_playback(PyJuceAudioHost* self) {
    std::string response = self->host->sendCommand("start_playback:");
    return PyUnicode_FromString(response.c_str());
}

static PyObject* PyJuceAudioHost_stop_playback(PyJuceAudioHost* self) {
    std::string response = self->host->sendCommand("stop_playback:");
    return PyUnicode_FromString(response.c_str());
}

static PyObject* PyJuceAudioHost_send_raw_command(PyJuceAudioHost* self, PyObject* args) {
    const char* command;
    
    if (!PyArg_ParseTuple(args, "s", &command)) {
        return nullptr;
    }
    
    std::string response = self->host->sendCommand(command);
    return PyUnicode_FromString(response.c_str());
}

// Method definitions
static PyMethodDef PyJuceAudioHost_methods[] = {
    {"connect", (PyCFunction)PyJuceAudioHost_connect, METH_NOARGS, "Connect to the GUI server"},
    {"disconnect", (PyCFunction)PyJuceAudioHost_disconnect, METH_NOARGS, "Disconnect from the GUI server"},
    {"load_plugin", (PyCFunction)PyJuceAudioHost_load_plugin, METH_VARARGS, "Load a plugin"},
    {"show_plugin_ui", (PyCFunction)PyJuceAudioHost_show_plugin_ui, METH_VARARGS, "Show plugin UI"},
    {"hide_plugin_ui", (PyCFunction)PyJuceAudioHost_hide_plugin_ui, METH_VARARGS, "Hide plugin UI"},
    {"set_parameter", (PyCFunction)PyJuceAudioHost_set_parameter, METH_VARARGS, "Set plugin parameter"},
    {"get_parameter", (PyCFunction)PyJuceAudioHost_get_parameter, METH_VARARGS, "Get plugin parameter"},
    {"connect_audio", (PyCFunction)PyJuceAudioHost_connect_audio, METH_VARARGS, "Connect audio between plugins"},
    {"connect_midi", (PyCFunction)PyJuceAudioHost_connect_midi, METH_VARARGS, "Connect MIDI between plugins"},
    {"start_playback", (PyCFunction)PyJuceAudioHost_start_playback, METH_NOARGS, "Start playback"},
    {"stop_playback", (PyCFunction)PyJuceAudioHost_stop_playback, METH_NOARGS, "Stop playback"},
    {"send_raw_command", (PyCFunction)PyJuceAudioHost_send_raw_command, METH_VARARGS, "Send raw command to server"},
    {nullptr}
};

// Type definition
static PyTypeObject PyJuceAudioHostType = {
    PyVarObject_HEAD_INIT(nullptr, 0)
    .tp_name = "juce_audio.JuceAudioHost",
    .tp_doc = "JUCE Audio Plugin Host",
    .tp_basicsize = sizeof(PyJuceAudioHost),
    .tp_itemsize = 0,
    .tp_flags = Py_TPFLAGS_DEFAULT | Py_TPFLAGS_BASETYPE,
    .tp_new = PyJuceAudioHost_new,
    .tp_init = (initproc)PyJuceAudioHost_init,
    .tp_dealloc = (destructor)PyJuceAudioHost_dealloc,
    .tp_methods = PyJuceAudioHost_methods,
};

// Module definition
static PyModuleDef juce_audio_module = {
    PyModuleDef_HEAD_INIT,
    .m_name = "juce_audio",
    .m_doc = "Python bindings for JUCE audio plugin hosting",
    .m_size = -1,
};

// Module initialization
PyMODINIT_FUNC PyInit_juce_audio(void) {
    PyObject* m;
    
    if (PyType_Ready(&PyJuceAudioHostType) < 0)
        return nullptr;
    
    m = PyModule_Create(&juce_audio_module);
    if (m == nullptr)
        return nullptr;
    
    Py_INCREF(&PyJuceAudioHostType);
    if (PyModule_AddObject(m, "JuceAudioHost", (PyObject*)&PyJuceAudioHostType) < 0) {
        Py_DECREF(&PyJuceAudioHostType);
        Py_DECREF(m);
        return nullptr;
    }
    
    return m;
}