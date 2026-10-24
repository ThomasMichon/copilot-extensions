#define _GNU_SOURCE
#include <dlfcn.h>
#include <fcntl.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/socket.h>
#include <sys/un.h>
#include <time.h>
#include <unistd.h>

#ifndef ENTRY_SHA
#error ENTRY_SHA required
#endif
typedef void PyObject;
typedef int (*audit_fn)(const char *, PyObject *, void *);
static int exe_fd = -1, handled = 0;
static _Thread_local int busy = 0;
static PyObject *source_dispatch = NULL;
static long long stamp(void) {
    struct timespec value;
    clock_gettime(CLOCK_MONOTONIC, &value);
    return (long long)value.tv_sec * 1000000000LL + value.tv_nsec;
}
static void publish(const char *kind) {
    const char *path = getenv("CGS_ENTRY_SOCKET");
    if (!path) return;
    int fd = socket(AF_UNIX, SOCK_DGRAM | SOCK_NONBLOCK | SOCK_CLOEXEC, 0);
    if (fd < 0) return;
    struct sockaddr_un address = { .sun_family = AF_UNIX };
    if (strlen(path) >= sizeof(address.sun_path)) { close(fd); return; }
    strcpy(address.sun_path, path);
    char data[160];
    int length = snprintf(data, sizeof(data), "%s %d %d %lld",
                          kind, getpid(), getppid(), stamp());
    char control[CMSG_SPACE(sizeof(int))] = {0};
    struct iovec iov = { .iov_base = data, .iov_len = (size_t)length };
    struct msghdr message = { .msg_name = &address, .msg_namelen = sizeof(address),
                             .msg_iov = &iov, .msg_iovlen = 1,
                             .msg_control = control, .msg_controllen = sizeof(control) };
    struct cmsghdr *header = CMSG_FIRSTHDR(&message);
    header->cmsg_level = SOL_SOCKET;
    header->cmsg_type = SCM_RIGHTS;
    header->cmsg_len = CMSG_LEN(sizeof(int));
    memcpy(CMSG_DATA(header), &exe_fd, sizeof(exe_fd));
    sendmsg(fd, &message, MSG_DONTWAIT | MSG_NOSIGNAL);
    close(fd);
}
static int audit(const char *event, PyObject *args, void *context) {
    (void)context;
    if (busy) return 0;
    if (handled) {
        if (source_dispatch && (!strcmp(event, "compile") || !strcmp(event, "exec"))) {
            PyObject *(*frame)(void) = dlsym(RTLD_DEFAULT, "PyEval_GetFrame");
            PyObject *(*code)(PyObject *) = dlsym(RTLD_DEFAULT, "PyFrame_GetCode");
            int (*lasti)(PyObject *) = dlsym(RTLD_DEFAULT, "PyFrame_GetLasti");
            PyObject *(*unicode)(const char *) = dlsym(RTLD_DEFAULT, "PyUnicode_FromString");
            PyObject *(*integer)(long) = dlsym(RTLD_DEFAULT, "PyLong_FromLong");
            PyObject *(*call)(PyObject *, ...) = dlsym(RTLD_DEFAULT, "PyObject_CallFunctionObjArgs");
            void (*decref)(PyObject *) = dlsym(RTLD_DEFAULT, "Py_DecRef");
            void (*clear)(void) = dlsym(RTLD_DEFAULT, "PyErr_Clear");
            busy = 1;
            PyObject *active = frame();
            if (active) {
                PyObject *origin = code(active), *name = unicode(event), *offset = integer(lasti(active));
                PyObject *result = call(source_dispatch, name, args, origin, offset, NULL);
                if (result) decref(result);
                else { clear(); publish("SOURCE_EVENT_FAILED"); }
                decref(origin); decref(name); decref(offset);
            } else publish("NATIVE_EMITTER_NO_PYTHON_FRAME");
            busy = 0;
        }
        return 0;
    }
    int enter = !strcmp(event, "cpython.run_command") ||
                !strcmp(event, "cpython.run_module") ||
                !strcmp(event, "cpython.run_file");
    if (!enter && !strcmp(event, "exec")) {
        PyObject *(*get_item)(PyObject *, long) = dlsym(RTLD_DEFAULT, "PyTuple_GetItem");
        PyObject *(*get_attr)(PyObject *, const char *) = dlsym(RTLD_DEFAULT, "PyObject_GetAttrString");
        const char *(*utf8)(PyObject *) = dlsym(RTLD_DEFAULT, "PyUnicode_AsUTF8");
        void (*decref)(PyObject *) = dlsym(RTLD_DEFAULT, "Py_DecRef");
        void (*clear)(void) = dlsym(RTLD_DEFAULT, "PyErr_Clear");
        busy = 1;
        PyObject *code = get_item(args, 0);
        PyObject *filename = code ? get_attr(code, "co_filename") : NULL;
        const char *name = filename ? utf8(filename) : NULL;
        enter = name && (strstr(name, "/fleet_manager/") || strstr(name, "\\fleet_manager\\"));
        if (filename) decref(filename);
        clear();
        busy = 0;
    }
    if (!enter) return 0;
    handled = busy = 1;
    PyObject *(*new_dict)(void) = dlsym(RTLD_DEFAULT, "PyDict_New");
    PyObject *(*builtins)(void) = dlsym(RTLD_DEFAULT, "PyEval_GetBuiltins");
    PyObject *(*unicode)(const char *) = dlsym(RTLD_DEFAULT, "PyUnicode_FromString");
    int (*set)(PyObject *, const char *, PyObject *) = dlsym(RTLD_DEFAULT, "PyDict_SetItemString");
    PyObject *(*run)(const char *, int, PyObject *, PyObject *, void *) = dlsym(RTLD_DEFAULT, "PyRun_StringFlags");
    void (*decref)(PyObject *) = dlsym(RTLD_DEFAULT, "Py_DecRef");
    void (*clear)(void) = dlsym(RTLD_DEFAULT, "PyErr_Clear");
    PyObject *globals = new_dict();
    PyObject *name = unicode("_cgs_native_entry");
    set(globals, "__builtins__", builtins());
    set(globals, "__name__", name);
    decref(name);
    const char *source =
        "__import__('os').environ['_CGS_PRELUDE_NS']=str(__import__('time').perf_counter_ns())\n"
        "import os,time\n"
        "if os.environ.get('CGS_PRELUDE_PHASE_PROFILE')=='1': os.environ['_CGS_PRELUDE_CPU_NS']=str(time.process_time_ns())\n"
        "import hashlib\n"
        "with open('/work/native-entry.py','rb') as file: source=file.read()\n"
        "if hashlib.sha256(source).hexdigest()!='" ENTRY_SHA "': raise RuntimeError('native entry source identity differs')\n"
        "exec(compile(source,'/work/native-entry.py','exec',dont_inherit=True),globals(),globals())\n";
    PyObject *result = run(source, 257, globals, globals, NULL);
    if (result) {
        PyObject *(*get)(PyObject *, const char *) = dlsym(RTLD_DEFAULT, "PyDict_GetItemString");
        void (*incref)(PyObject *) = dlsym(RTLD_DEFAULT, "Py_IncRef");
        source_dispatch = get(globals, "native_source_event");
        if (source_dispatch) incref(source_dispatch);
        decref(result); publish("ENTRY_READY");
    }
    else { clear(); publish("ENTRY_FAILED"); }
    decref(globals);
    busy = 0;
    return 0;
}
__attribute__((constructor)) static void begin(void) {
    exe_fd = open("/proc/self/exe", O_RDONLY | O_CLOEXEC);
    int (*add)(audit_fn, void *) = dlsym(RTLD_DEFAULT, "PySys_AddAuditHook");
    publish(add ? "PYTHON_CONSTRUCTOR" : "NATIVE_CONSTRUCTOR");
    if (add && add(audit, NULL) != 0) publish("AUDIT_REGISTRATION_FAILED");
}
