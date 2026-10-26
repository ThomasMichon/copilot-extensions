/* Linux kernel binding for the scalar instance protocol; no service or scan. */
#define _GNU_SOURCE
#include <sys/types.h>
#include <sys/stat.h>
#include <sys/file.h>
#include <sys/prctl.h>
#include <sys/syscall.h>
#include <sys/wait.h>
#include <poll.h>
#include <fcntl.h>
#include <signal.h>
#include <unistd.h>
#include <errno.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include <limits.h>
#include <stdint.h>

#ifndef SYS_pidfd_open
#define SYS_pidfd_open 434
#endif
#ifndef SYS_pidfd_send_signal
#define SYS_pidfd_send_signal 424
#endif

#define FIELD_COUNT 256
#define RECORD_LIMIT 65536
typedef struct { char *key; char *value; } field;
typedef struct { field values[FIELD_COUNT]; size_t count; } record;
static const char alphabet[]="ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";

static void release(record *r) {
    for (size_t i=0;i<r->count;i++) { free(r->values[i].key); free(r->values[i].value); }
    r->count=0;
}
static const char *get(record *r,const char *key) {
    for (size_t i=0;i<r->count;i++) if (!strcmp(r->values[i].key,key)) return r->values[i].value;
    return NULL;
}
static int put(record *r,const char *key,const char *value) {
    for (size_t i=0;i<r->count;i++) if (!strcmp(r->values[i].key,key)) {
        char *copy=strdup(value); if (!copy) return -1;
        free(r->values[i].value); r->values[i].value=copy; return 0;
    }
    if (r->count==FIELD_COUNT) return -1;
    char *k=strdup(key),*v=strdup(value);
    if (!k || !v) { free(k); free(v); return -1; }
    r->values[r->count++]=(field){k,v}; return 0;
}
static char *decode(const char *value) {
    size_t length=strlen(value), output=0;
    if (length%4) return NULL;
    char *result=calloc(length+1,1); if (!result) return NULL;
    for (size_t at=0;at<length;at+=4) {
        unsigned bits=0; int padding=0;
        for (int i=0;i<4;i++) {
            const char *part=strchr(alphabet,value[at+i]);
            if (value[at+i]=='=') { padding++; bits<<=6; }
            else if (!part || padding) { free(result); return NULL; }
            else bits=(bits<<6)|(unsigned)(part-alphabet);
        }
        if (padding>2 || (padding && at+4!=length)) { free(result); return NULL; }
        for (int i=0;i<3-padding;i++) {
            char byte=(char)(bits>>(16-i*8));
            if (!byte || byte=='\n' || byte=='\r') { free(result); return NULL; }
            result[output++]=byte;
        }
    }
    return result;
}
static char *encode(const char *value) {
    size_t length=strlen(value), output=0;
    char *result=calloc(((length+2)/3)*4+1,1); if (!result) return NULL;
    for (size_t at=0;at<length;at+=3) {
        unsigned bits=(unsigned char)value[at]<<16;
        if (at+1<length) bits|=(unsigned char)value[at+1]<<8;
        if (at+2<length) bits|=(unsigned char)value[at+2];
        result[output++]=alphabet[(bits>>18)&63]; result[output++]=alphabet[(bits>>12)&63];
        result[output++]=at+1<length?alphabet[(bits>>6)&63]:'=';
        result[output++]=at+2<length?alphabet[bits&63]:'=';
    }
    return result;
}
static int plain_entry(const char *path,int directory,int owned) {
    struct stat info; char canonical[PATH_MAX];
    if (!path || path[0]!='/' || !realpath(path,canonical) || strcmp(canonical,path) ||
        lstat(path,&info) || (owned && info.st_uid!=geteuid())) return -1;
    return directory ? (S_ISDIR(info.st_mode)?0:-1) : (S_ISREG(info.st_mode)?0:-1);
}
static int plain(const char *path,int directory) { return plain_entry(path,directory,1); }
static int read_record(const char *path,record *r) {
    if (plain(path,0)) return -1;
    int fd=open(path,O_RDONLY|O_CLOEXEC|O_NOFOLLOW); if (fd<0) return -1;
    struct stat info; char buffer[RECORD_LIMIT+1];
    if (fstat(fd,&info) || info.st_size>RECORD_LIMIT) { close(fd); return -1; }
    size_t used=0;
    while (used<RECORD_LIMIT) {
        ssize_t n=read(fd,buffer+used,RECORD_LIMIT-used);
        if (n<0 && errno==EINTR) continue;
        if (n<0) { close(fd); return -1; }
        if (!n) break;
        used+=(size_t)n;
    }
    close(fd);
    if (memchr(buffer,0,used)) return -1;
    buffer[used]=0;
    char *save=NULL,*line=strtok_r(buffer,"\n",&save);
    while (line) {
        char *tab=strchr(line,'\t');
        if (!tab || strchr(tab+1,'\t')) return -1;
        *tab++=0;
        if (!*line || strlen(line)>64 || line[0]<'a' || line[0]>'z' || get(r,line)) return -1;
        for (char *p=line;*p;p++) if (!((*p>='a'&&*p<='z')||(*p>='0'&&*p<='9')||*p=='_')) return -1;
        char *value=decode(tab); if (!value) return -1;
        int status=put(r,line,value); free(value); if (status) return -1;
        line=strtok_r(NULL,"\n",&save);
    }
    const char *id=get(r,"instance_id"),*schema=get(r,"schema");
    const char *base=strrchr(path,'/'); base=base?base+1:path;
    size_t base_length=strlen(base);
    if (!id || strlen(id)!=32 || !schema || strcmp(schema,"1") ||
        (base_length!=41 && base_length!=40) ||
        strncmp(base,id,32) || (strcmp(base+32,".instance") && strcmp(base+32,".settled"))) return -1;
    for (int i=0;i<32;i++) if (!((id[i]>='0'&&id[i]<='9')||(id[i]>='a'&&id[i]<='f'))) return -1;
    return 0;
}
static int write_record(const char *path,record *r) {
    char pending[PATH_MAX]; int n=snprintf(pending,sizeof pending,"%s.%ld.pending",path,(long)getpid());
    if (n<0 || (size_t)n>=sizeof pending) return -1;
    int fd=open(pending,O_WRONLY|O_CREAT|O_EXCL|O_CLOEXEC|O_NOFOLLOW,0600);
    if (fd<0) return -1;
    FILE *file=fdopen(fd,"w"); if (!file) { close(fd); unlink(pending); return -1; }
    int failed=0; size_t bytes=0;
    for (size_t i=0;i<r->count;i++) {
        char *value=encode(r->values[i].value); if (!value) { failed=1; break; }
        bytes+=strlen(r->values[i].key)+strlen(value)+2;
        if (bytes>RECORD_LIMIT || fprintf(file,"%s\t%s\n",r->values[i].key,value)<0) failed=1;
        free(value); if (failed) break;
    }
    if (fflush(file) || fsync(fd)) failed=1;
    if (fclose(file)) failed=1;
    struct stat existing;
    if (!failed && (!lstat(path,&existing) ? plain(path,0) : errno!=ENOENT)) failed=1;
    if (!failed && rename(pending,path)) failed=1;
    if (failed) unlink(pending);
    return failed?-1:0;
}
static int lock_record(const char *path) {
    char lock[PATH_MAX]; size_t length=strlen(path);
    if (length<9 || length>=sizeof lock) return -1;
    memcpy(lock,path,length-9); strcpy(lock+length-9,".lock");
    if (plain(lock,0)) return -1;
    int fd=open(lock,O_RDWR|O_CLOEXEC|O_NOFOLLOW);
    if (fd<0) return -1;
    if (flock(fd,LOCK_EX|LOCK_NB)) { close(fd); return -1; }
    return fd;
}
static double seconds(clockid_t clock) {
    struct timespec now; if (clock_gettime(clock,&now)) return -1;
    return (double)now.tv_sec+(double)now.tv_nsec/1e9;
}
static double number(record *r,const char *name) {
    const char *value=get(r,name); if (!value || !*value) return -1;
    char *end; errno=0; double result=strtod(value,&end);
    if (*end || errno || !(result>=0 && result<1e15)) return -1;
    return result;
}
static int disposable(record *r) {
    const char *purpose=get(r,"purpose");
    return purpose && (!strcmp(purpose,"unattended-test") || !strcmp(purpose,"interactive-test"));
}
static int pid_value(record *r,const char *name) {
    double value=number(r,name);
    return value>0 && value<INT_MAX && (int)value==value ? (int)value : -1;
}
/* 0 live exact token; 1 known absent/zombie; -1 unknown. */
static int birth(pid_t pid,char *out,size_t length) {
    char path[128],buffer[8192],boot[128]; struct stat ns;
    snprintf(path,sizeof path,"/proc/%ld/stat",(long)pid);
    int fd=open(path,O_RDONLY|O_CLOEXEC);
    if (fd<0) return errno==ENOENT?1:-1;
    ssize_t n=read(fd,buffer,sizeof buffer-1); close(fd);
    if (n<=0 || n==(ssize_t)sizeof buffer-1) return -1;
    buffer[n]=0; char *fields=strrchr(buffer,')'); if (!fields || fields[1]!=' ') return -1;
    fields+=2; if (*fields=='Z') return 1;
    char *save=NULL,*token=strtok_r(fields," ",&save); unsigned long long ticks=0;
    for (int i=0;i<=19;i++) {
        if (!token) return -1;
        if (i==19) { char *end; errno=0; ticks=strtoull(token,&end,10); if (*end||errno) return -1; }
        token=strtok_r(NULL," ",&save);
    }
    fd=open("/proc/sys/kernel/random/boot_id",O_RDONLY|O_CLOEXEC);
    if (fd<0) return -1;
    n=read(fd,boot,sizeof boot-1); close(fd); if (n<=0) return -1;
    boot[n]=0; boot[strcspn(boot,"\r\n")]=0;
    snprintf(path,sizeof path,"/proc/%ld/ns/pid",(long)pid);
    if (stat(path,&ns)) return errno==ENOENT?1:-1;
    int written=snprintf(out,length,"linux:%s:%llu:%llu",boot,(unsigned long long)ns.st_ino,ticks);
    return written<0 || (size_t)written>=length?-1:0;
}
static int pidfd_open_native(pid_t pid) {
    return (int)syscall(SYS_pidfd_open,pid,0);
}
static int exited(int fd,int milliseconds) {
    struct pollfd p={fd,POLLIN,0};
    int result; do { result=poll(&p,1,milliseconds); } while (result<0 && errno==EINTR);
    return result>0 && (p.revents&POLLIN);
}
static int root_matches(record *r) {
    const char *root=get(r,"runtime_root"); struct stat info; char dev[64],ino[64];
    if (plain_entry(root,1,0) || stat(root,&info)) return 0;
    snprintf(dev,sizeof dev,"%llu",(unsigned long long)info.st_dev);
    snprintf(ino,sizeof ino,"%llu",(unsigned long long)info.st_ino);
    return get(r,"runtime_device") && get(r,"runtime_inode") &&
        !strcmp(dev,get(r,"runtime_device")) && !strcmp(ino,get(r,"runtime_inode"));
}
static int settlement(const char *path,record *r,int remove_proof) {
    char witness[PATH_MAX]; size_t length=strlen(path);
    if (length<9 || length>=sizeof witness) return -1;
    memcpy(witness,path,length-9); strcpy(witness+length-9,".settled");
    record proof={0}; int valid=read_record(witness,&proof)==0;
    const char *keys[]={"pid","birth","supervisor_pid","supervisor_birth"};
    for (size_t i=0;valid && i<4;i++)
        valid=get(&proof,keys[i]) && get(r,keys[i]) && !strcmp(get(&proof,keys[i]),get(r,keys[i]));
    valid=valid && get(&proof,"tree_settled") && !strcmp(get(&proof,"tree_settled"),"1");
    release(&proof);
    if (!valid) return -1;
    return remove_proof?unlink(witness):0;
}
static int artifact(record *r,int index,int remove_file,const char *record_path) {
    char key[80],dev[64],ino[64],canonical[PATH_MAX]; struct stat info;
    snprintf(key,sizeof key,"artifact_%d_path",index); const char *path=get(r,key);
    const char *root=get(r,"runtime_root"),*source=get(r,"source");
    const char *parent_end=strrchr(record_path,'/');
    size_t inventory_length=parent_end?(size_t)(parent_end-record_path):0;
    if (!path || !root || !source || !strcmp(source,path) ||
        strncmp(path,root,strlen(root)) || path[strlen(root)]!='/' ||
        (inventory_length && !strncmp(path,record_path,inventory_length) &&
         path[inventory_length]=='/')) return -1;
    if (lstat(path,&info)) return errno==ENOENT?0:-1;
    if (plain(path,0) || !realpath(path,canonical) || strcmp(canonical,path)) return -1;
    snprintf(dev,sizeof dev,"%llu",(unsigned long long)info.st_dev);
    snprintf(ino,sizeof ino,"%llu",(unsigned long long)info.st_ino);
    snprintf(key,sizeof key,"artifact_%d_device",index);
    if (!get(r,key) || strcmp(dev,get(r,key))) return -1;
    snprintf(key,sizeof key,"artifact_%d_inode",index);
    if (!get(r,key) || strcmp(ino,get(r,key))) return -1;
    return remove_file?unlink(path):0;
}
static void report(record *r,const char *status,const char *reason) {
    const char *id=get(r,"instance_id");
    printf("%s\t%s\t%s\n",status,reason,id?id:"unknown");
    if (!strcmp(status,"held")) fprintf(stderr,"daemon instance held: %s %s\n",id?id:"unknown",reason);
}
static int cleanup(const char *path,double timeout) {
    record r={0}; int lock=lock_record(path),process=-1,supervisor=-1;
    const char *reason="invalid-record"; int rc=1; double end=seconds(CLOCK_MONOTONIC)+timeout;
    if (lock<0 || read_record(path,&r)) goto done;
    if (get(&r,"purpose") && !strcmp(get(&r,"purpose"),"production")) {
        if (get(&r,"lifecycle") && !strcmp(get(&r,"lifecycle"),"finite-worker")) {
            const char *state=get(&r,"state");
            if (!state || (strcmp(state,"reserved") && strcmp(state,"bound") &&
                strcmp(state,"running") && strcmp(state,"finished") && strcmp(state,"cleaning"))) {
                reason="unknown-state"; goto done;
            }
            if (get(&r,"spawn_failure") || (get(&r,"protected") && strcmp(get(&r,"protected"),"0"))) {
                reason="finite-worker-held"; goto done;
            }
            int pid=pid_value(&r,"pid"); char token[512];
            if (pid<1 || !get(&r,"birth")) { reason="launch-outcome-unknown"; goto done; }
            process=pidfd_open_native(pid);
            if (process<0 && errno!=ESRCH) { reason="process-unknown"; goto done; }
            int observed=birth(pid,token,sizeof token);
            if (observed<0) { reason="process-unknown"; goto done; }
            if (!observed && strcmp(token,get(&r,"birth"))) { reason="process-reused"; goto done; }
            if (!observed && !exited(process,0)) {
                report(&r,"live","finite-worker-running"); rc=0; goto exit;
            }
            if (unlink(path)) { reason="record-removal-unconfirmed"; goto done; }
            report(&r,"settled","finite-worker-exit-observed"); rc=0; goto exit;
        }
        report(&r,"live","production-or-unexpired"); rc=0; goto exit;
    }
    if (!disposable(&r) || !get(&r,"state") || !get(&r,"service") || !get(&r,"source")) goto done;
    double deadline=number(&r,"deadline"),hard=number(&r,"hard_deadline");
    if (!(deadline>0 && hard>=deadline)) goto done;
    if (seconds(CLOCK_REALTIME)<deadline) { report(&r,"live","production-or-unexpired"); rc=0; goto exit; }
    if (get(&r,"protected") && strcmp(get(&r,"protected"),"0")) { reason="protected-work"; goto done; }
    if (!root_matches(&r)) { reason="runtime-root-changed"; goto done; }
    int count=(int)number(&r,"artifact_count"); if (count<0 || count>64) goto done;
    for (int i=0;i<count;i++) if (artifact(&r,i,0,path)) { reason="artifact-identity-changed"; goto done; }
    int pid=pid_value(&r,"pid"); char token[512];
    if (pid<1 || !get(&r,"birth")) { reason="launch-outcome-unknown"; goto done; }
    process=pidfd_open_native(pid);
    if (process<0 && errno!=ESRCH) { reason="process-unknown"; goto done; }
    int observed=birth(pid,token,sizeof token);
    if (observed<0) { reason="process-unknown"; goto done; }
    if (!observed && strcmp(token,get(&r,"birth"))) { reason="process-reused"; goto done; }
    int supervisor_pid=pid_value(&r,"supervisor_pid");
    if (supervisor_pid>0) {
        supervisor=pidfd_open_native(supervisor_pid);
        int observed_supervisor=birth(supervisor_pid,token,sizeof token);
        if (observed_supervisor<0 || (!observed_supervisor &&
            (!get(&r,"supervisor_birth") || strcmp(token,get(&r,"supervisor_birth"))))) {
            reason="supervisor-identity-unknown"; goto done;
        }
        if (supervisor>=0 && !exited(supervisor,0)) {
            if (syscall(SYS_pidfd_send_signal,supervisor,SIGTERM,NULL,0) && errno!=ESRCH) {
                reason="stop-unconfirmed"; goto done;
            }
            int remaining=(int)((end-seconds(CLOCK_MONOTONIC))*1000);
            if (!exited(supervisor,remaining>0?remaining:0)) {
                reason="descendant-settlement-unconfirmed"; goto done;
            }
        }
    }
    if (!observed && process>=0) {
        if (pid==getpid()) { reason="self-cleanup-refused"; goto done; }
        if (supervisor_pid<1 && syscall(SYS_pidfd_send_signal,process,SIGKILL,NULL,0) && errno!=ESRCH) {
            reason="stop-unconfirmed"; goto done;
        }
        int remaining=(int)((end-seconds(CLOCK_MONOTONIC))*1000);
        if (!exited(process,remaining>0?remaining:0)) { reason="stop-unconfirmed"; goto done; }
    }
    if (supervisor>=0 && !exited(supervisor,0)) {
        reason="descendant-settlement-unconfirmed"; goto done;
    }
    if (supervisor_pid>0 && (!get(&r,"tree_settled") || strcmp(get(&r,"tree_settled"),"1")) &&
        settlement(path,&r,0)) {
        reason="descendant-settlement-unconfirmed"; goto done;
    }
    if (supervisor_pid>0 && (put(&r,"tree_settled","1") || write_record(path,&r))) {
        reason="record-publication-unconfirmed"; goto done;
    }
    for (int i=0;i<count;i++) if (artifact(&r,i,1,path)) { reason="artifact-removal-unconfirmed"; goto done; }
    if (supervisor_pid>0) settlement(path,&r,1);
    if (unlink(path)) { reason="record-removal-unconfirmed"; goto done; }
    report(&r,"settled","process-exit-observed"); rc=0; goto exit;
done:
    report(&r,"held",reason);
exit:
    if (process>=0) close(process);
    if (supervisor>=0) close(supervisor);
    if (lock>=0) close(lock);
    release(&r); return rc;
}
static volatile sig_atomic_t interrupted=0;
static void interrupt_handler(int sig) { (void)sig; interrupted=1; }

static int retire_owned_children(void) {
    char path[128],buffer[RECORD_LIMIT+1];
    snprintf(path,sizeof path,"/proc/self/task/%ld/children",(long)getpid());
    int fd=open(path,O_RDONLY|O_CLOEXEC); if (fd<0) return -1;
    ssize_t count=read(fd,buffer,RECORD_LIMIT); close(fd);
    if (count<0 || count==RECORD_LIMIT) return -1;
    buffer[count]=0;
    char *cursor=buffer;
    while (*cursor) {
        while (*cursor==' ') cursor++;
        if (!*cursor) break;
        char *end; errno=0; long pid=strtol(cursor,&end,10);
        if (end==cursor || errno || pid<1 || pid>INT_MAX) return -1;
        cursor=end;
        int child=pidfd_open_native((pid_t)pid);
        if (child<0) { if (errno==ESRCH) continue; return -1; }
        /* No child is reaped during this census. Kernel parent custody pins
           its identity even if it exits before the pidfd signal. */
        snprintf(path,sizeof path,"/proc/%ld/stat",pid);
        int stat_fd=open(path,O_RDONLY|O_CLOEXEC);
        if (stat_fd<0) { close(child); return -1; }
        char data[8192]; ssize_t length=read(stat_fd,data,sizeof data-1); close(stat_fd);
        if (length<=0 || length==(ssize_t)sizeof data-1) { close(child); return -1; }
        data[length]=0; char *fields=strrchr(data,')');
        if (!fields || fields[1]!=' ') { close(child); return -1; }
        char state; long parent;
        if (sscanf(fields+2,"%c %ld",&state,&parent)!=2 || parent!=(long)getpid()) {
            close(child); return -1;
        }
        if (!exited(child,0) && syscall(SYS_pidfd_send_signal,child,SIGKILL,NULL,0) && errno!=ESRCH) {
            close(child); return -1;
        }
        close(child);
    }
    return 0;
}

static int supervise(const char *path,char **command,int ready_fd) {
    record r={0}; int lock=lock_record(path),owner=-1; pid_t child=-1;
    int gate[2]={-1,-1}; char token[512],value[64]; int rc=125,settled=0,custody_valid=1;
    if (lock<0 || read_record(path,&r) || !root_matches(&r)) goto done;
    if (!disposable(&r) || !get(&r,"service") || !*get(&r,"service") ||
        !get(&r,"source") || get(&r,"source")[0]!='/') goto done;
    struct stat source;
    if (stat(get(&r,"source"),&source) || !S_ISREG(source.st_mode)) goto done;
    const char *state=get(&r,"state");
    if (!state || (strcmp(state,"reserved") && strcmp(state,"bound"))) goto done;
    if (!strcmp(state,"bound")) {
        if (pid_value(&r,"pid")!=getpid() || birth(getpid(),token,sizeof token) ||
            !get(&r,"birth") || strcmp(token,get(&r,"birth"))) goto done;
    } else {
        int launcher=pid_value(&r,"launcher_pid");
        if ((launcher!=getpid() && launcher!=getppid()) ||
            birth(launcher,token,sizeof token) || !get(&r,"launcher_birth") ||
            strcmp(token,get(&r,"launcher_birth"))) goto done;
    }
    double deadline=number(&r,"deadline"),hard=number(&r,"hard_deadline");
    double now=seconds(CLOCK_REALTIME),hard_mono=seconds(CLOCK_MONOTONIC)+(hard-now);
    if (!(deadline>now && hard>=deadline)) goto done;
    int owner_pid=pid_value(&r,"owner_pid");
    owner=pidfd_open_native(owner_pid);
    if (owner<0 || birth(owner_pid,token,sizeof token) || !get(&r,"owner_birth") ||
        strcmp(token,get(&r,"owner_birth"))) goto done;
    /* A finite owned group, not a detached session or independent service.
       The pidfd lifecycle owner remains authoritative when its launcher exits. */
    if (setpgid(0,0) && errno!=EACCES && getpgrp()!=getpid()) goto done;
    if (prctl(PR_SET_CHILD_SUBREAPER,1) || pipe2(gate,O_CLOEXEC)) goto done;
    struct sigaction action={0}; action.sa_handler=interrupt_handler; sigemptyset(&action.sa_mask);
    sigaction(SIGTERM,&action,NULL); sigaction(SIGINT,&action,NULL); sigaction(SIGHUP,&action,NULL);
    signal(SIGPIPE,SIG_IGN);
    signal(SIGCHLD,SIG_DFL);
    child=fork(); if (child<0) goto done;
    if (!child) {
        close(gate[1]);
        if (setpgid(0,0)) _exit(125);
        char byte;
        if (read(gate[0],&byte,1)!=1 || byte!=1) _exit(125);
        close(gate[0]);
        close(lock); close(owner); if (ready_fd>=0) close(ready_fd);
        execvp(command[0],command);
        _exit(127);
    }
    close(gate[0]); gate[0]=-1;
    if (setpgid(child,child) && errno!=EACCES) goto retire;
    if (birth(child,token,sizeof token)) goto retire;
    snprintf(value,sizeof value,"%ld",(long)child); if (put(&r,"pid",value)||put(&r,"birth",token)) goto retire;
    snprintf(value,sizeof value,"%ld",(long)getpid());
    if (put(&r,"supervisor_pid",value) || birth(getpid(),token,sizeof token) ||
        put(&r,"supervisor_birth",token) || put(&r,"state","running") || write_record(path,&r)) goto retire;
    close(lock); lock=-1;
    /* The wrapper's existing admission FIFO names the actual application child. */
    if (ready_fd>=0) {
        if (dprintf(ready_fd,"ready:%ld\n",(long)child)<0) goto retire;
        close(ready_fd);
    }
    if (write(gate[1],"\1",1)!=1) goto retire;
    close(gate[1]); gate[1]=-1;
    double seen_deadline=deadline;
    double lease_mono=seconds(CLOCK_MONOTONIC)+(deadline-now);
    rc=124;
    for (;;) {
        siginfo_t info={0};
        if (waitid(P_PID,(id_t)child,&info,WEXITED|WNOHANG|WNOWAIT)) { rc=125; break; }
        if (info.si_pid==child) {
            rc=info.si_code==CLD_EXITED?info.si_status:128+info.si_status;
            break;
        }
        double mono=seconds(CLOCK_MONOTONIC);
        if (interrupted || exited(owner,0) || mono>=hard_mono || mono>=lease_mono) break;
        lock=lock_record(path);
        if (lock>=0) {
            record update={0};
            if (read_record(path,&update) || !get(&update,"birth") ||
                strcmp(get(&update,"birth"),get(&r,"birth"))) {
                release(&update); break;
            }
            double renewed=number(&update,"deadline");
            if (!(renewed>0 && renewed<=hard)) { release(&update); break; }
            if (renewed!=seen_deadline) {
                seen_deadline=renewed;
                lease_mono=mono+(renewed-seconds(CLOCK_REALTIME));
            }
            release(&update); close(lock); lock=-1;
        }
        struct timespec pause={0,50000000}; nanosleep(&pause,NULL);
    }
retire:
    /* The original child remains unreaped until this one group signal. */
    if (child>0) {
        kill(-child,SIGKILL);
        double stop=seconds(CLOCK_MONOTONIC)+5;
        for (;;) {
            if (retire_owned_children()) { rc=125; custody_valid=0; }
            int status; pid_t result=waitpid(-1,&status,WNOHANG);
            if (result<0 && errno==ECHILD) { settled=custody_valid; break; }
            if (result<0 && errno!=EINTR) { rc=125; break; }
            if (seconds(CLOCK_MONOTONIC)>=stop) { rc=125; break; }
            struct timespec pause={0,10000000}; nanosleep(&pause,NULL);
        }
    }
    if (settled && get(&r,"supervisor_pid")) {
        char witness[PATH_MAX]; size_t length=strlen(path);
        if (length>=9 && length<sizeof witness) {
            memcpy(witness,path,length-9); strcpy(witness+length-9,".settled");
            if (put(&r,"tree_settled","1") || write_record(witness,&r)) rc=1;
        } else rc=1;
    }
done:
    if (gate[0]>=0) close(gate[0]);
    if (gate[1]>=0) close(gate[1]);
    if (lock>=0) close(lock);
    if (owner>=0) close(owner);
    release(&r); return rc;
}
int main(int argc,char **argv) {
    if (argc==2 && !strcmp(argv[1],"check")) {
        int fd=pidfd_open_native(getpid());
        if (fd<0) return 1;
        int result=(int)syscall(SYS_pidfd_send_signal,fd,0,NULL,0);
        close(fd); return result?1:0;
    }
    if (argc>=5 && !strcmp(argv[1],"supervise")) {
        int ready=-1,offset=3;
        if (!strcmp(argv[offset],"--ready-fd") && argc>=7) {
            char *end; long descriptor=strtol(argv[offset+1],&end,10);
            if (*end || descriptor<3 || descriptor>1024) return 2;
            ready=(int)descriptor; offset+=2;
        }
        if (strcmp(argv[offset],"--") || offset+1>=argc) return 2;
        return supervise(argv[2],argv+offset+1,ready);
    }
    if (argc==4 && !strcmp(argv[1],"cleanup")) {
        char *end; double timeout=strtod(argv[3],&end);
        if (*end || !(timeout>0 && timeout<=300)) return 2;
        return cleanup(argv[2],timeout);
    }
    fprintf(stderr,"native daemon instance binding: invalid invocation\n");
    return 2;
}
