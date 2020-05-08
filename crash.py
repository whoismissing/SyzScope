import os, re, stat
import logging
import argparse
import utilities
import time
import threading
import json

from subprocess import call, Popen, PIPE, STDOUT
from syzbotCrawler import Crawler

startup_regx = r'Debian GNU\/Linux \d+ syzkaller ttyS\d+'
boundary_regx = r'======================================================'
message_drop_regx = r'printk messages dropped'
kasan_regx = r'BUG: KASAN: ([a-z\\-]+) in ([a-zA-Z0-9_]+).*'
free_regx = r'BUG: KASAN: double-free or invalid-free in ([a-zA-Z0-9_]+).*'
default_port = 3777

class CrashChecker:
    def __init__(self, project_path, case_path, ssh_port, logger):
        os.makedirs("{}/poc".format(case_path), exist_ok=True)
        self.kasan_regx = r'KASAN: ([a-z\\-]+) Write in ([a-zA-Z0-9_]+).*'
        self.free_regx = r'KASAN: double-free or invalid-free in ([a-zA-Z0-9_]+).*'
        self.logger = logger
        self.project_path = project_path
        self.case_path = case_path
        self.image_path = "{}/tools/img".format(self.project_path)
        self.linux_path = "{}/linux".format(self.case_path)
        self.case_logger = self.__init_case_logger("{}-info".format(case_path))
        self.ssh_port = ssh_port
        self.kasan_func_list = self.read_kasan_funcs()

    def run(self, syz_repro, syz_commit, log=None, linux_commit=None, config=None):
        exitcode = self.deploy_ori_linux(linux_commit, config)
        if exitcode == 1:
            self.logger.info("Error occur at deploy-ori-linux-sh")
            return [False, None]
        ori_crash_report = self.read_ori_crash(syz_repro, syz_commit, log)
        if ori_crash_report == []:
            self.logger.info("No crash trigger by original poc")
            return [False, None]
        crashes_path = self.extract_existed_crash(self.case_path)
        for path in crashes_path:
            self.case_logger.info("Inspect crash: {}".format(path))
            new_crash_reports = self.read_existed_crash(path)
            if self.compare_crashes(ori_crash_report, new_crash_reports):
                return [True, path]
        return [False, None]
    
    def read_kasan_funcs(self):
        res = []
        path = os.path.join(self.project_path, "resources/kasan_related_funcs")
        with open(path, "r") as f:
            lines = f.readlines()
            for line in lines:
                res.append(line.strip('\n'))
            return res

    def compare_crashes(self, ori_crash_report, new_crash_reports):
        for report1 in ori_crash_report:
            if len(report1) > 2:
                for report2 in new_crash_reports:
                    if len(report2) > 2:
                        if self.__match_allocated_section(report1, report2):
                            return True
                        if self.__match_call_trace(report1, report2):
                            return True
        return False

    def extract_existed_crash(self, path):
        crash_path = os.path.join(path, "crashes")
        res = []

        if os.path.isdir(crash_path):
            for case in os.listdir(crash_path):
                description_file = "{}/{}/description".format(crash_path, case)
                if os.path.isfile(description_file):
                    with open(description_file, "r") as f:
                        line = f.readline()
                        if utilities.regx_match(self.kasan_regx, line):
                            res.append(os.path.join(crash_path, case))
                            continue
                        if utilities.regx_match(self.free_regx, line):
                            res.append(os.path.join(crash_path, case))
                            continue
        return res
    
    def read_ori_crash(self, syz_repro, syz_commit, log):
        if log != None:
            print("Go for log")
            res = self.read_from_log(log)
        else:
            print("Go for triggering crash")
            res = self.trigger_ori_crash(syz_repro, syz_commit)
        self.save_crash_log(res)
        return res
    
    def read_existed_crash(self, crash_path):
        res = []
        crash = []
        record_flag = 0
        kasan_flag = 0
        report_path = os.path.join(crash_path, "repro.log")
        if os.path.isfile(report_path):
            with open(report_path, "r") as f:
                lines = f.readlines()
                for line in lines:
                    if utilities.regx_match(boundary_regx, line) or \
                       utilities.regx_match(message_drop_regx, line):
                        record_flag ^= 1
                        if record_flag == 0 and kasan_flag == 1:
                            res.append(crash)
                            crash = []
                            kasan_flag ^= 1
                        continue
                    if utilities.regx_match(kasan_regx, line) or \
                       utilities.regx_match(free_regx, line):
                       kasan_flag ^= 1
                    if record_flag and kasan_flag:
                        crash.append(line)
        return res

    def read_from_log(self, log):
        res = []
        crash = []
        record_flag = 0
        kasan_flag = 0
        r = utilities.request_get(log)
        text = r.text.split('\n')
        for line in text:
            if utilities.regx_match(boundary_regx, line) or \
                utilities.regx_match(message_drop_regx, line):
                record_flag ^= 1
                if record_flag == 0 and kasan_flag == 1:
                    res.append(crash)
                    crash = []
                continue
            if utilities.regx_match(kasan_regx, line) or \
                utilities.regx_match(free_regx, line):
                kasan_flag ^= 1
            if record_flag and kasan_flag:
                crash.append(line)
        return res
        
    def save_crash_log(self, log):
        with open("{}/poc/crash_log".format(self.case_path), "w+") as f:
            for each in log:
                for line in each:
                    f.write(line+"\n")
                f.write("\n")
    
    def deploy_ori_linux(self, commit, config):
        utilities.chmodX("scripts/deploy-ori-linux.sh")
        patch_path = "{}/patches".format(self.project_path)
        p = None
        if commit == None and config == None:
            print("run: scripts/deploy-ori-linux.sh {} {}".format(self.linux_path, patch_path))
            p = Popen(["scripts/deploy-ori-linux.sh", self.linux_path, patch_path],
                stdout=PIPE,
                stderr=STDOUT)
        else:
            print("run: scripts/deploy-ori-linux.sh {} {} {} {}".format(self.linux_path, patch_path, commit, config))
            p = Popen(["scripts/deploy-ori-linux.sh", self.linux_path, patch_path, commit, config],
                stdout=PIPE,
                stderr=STDOUT)
        with p.stdout:
            self.__log_subprocess_output(p.stdout, logging.INFO)
        exitcode = p.wait()

    def trigger_ori_crash(self, syz_repro, syz_commit):
        res = []
        p = Popen(["qemu-system-x86_64", "-m", "2048M", "-smp", "2", "-net", "nic,model=e1000", "-enable-kvm", "-no-reboot",
                   "-cpu", "host", "-net", "user,host=10.0.2.10,hostfwd=tcp::{}-:22".format(self.ssh_port),
                   "-display", "none", "-serial", "stdio", "-no-reboot", "-hda", "{}/stretch.img".format(self.image_path), 
                   "-snapshot", "-kernel", "{}/arch/x86_64/boot/bzImage".format(self.linux_path),
                   "-append", "console=ttyS0 net.ifnames=0 root=/dev/sda printk.synchronous=1 kasan_multi_shot=1 oops=panic"],
                  stdout=PIPE,
                  stderr=STDOUT
                  )
        x = threading.Thread(target=self.monitor_execution, args=(p,))
        x.start()
        with p.stdout:
            extract_report = False
            record_flag = 0
            kasan_flag = 0
            crash = []
            for line in iter(p.stdout.readline, b''):
                line = line.decode("utf-8").strip('\n').strip('\r')
                self.case_logger.info(line)
                if utilities.regx_match(startup_regx, line):
                    utilities.chmodX("scripts/upload-exp.sh")
                    p2 = Popen(["scripts/upload-exp.sh", self.case_path, syz_repro, str(self.ssh_port), self.image_path, syz_commit],
                    stdout=PIPE,
                    stderr=STDOUT)
                    with p2.stdout:
                        self.__log_subprocess_output(p2.stdout, logging.INFO)
                    if p2.wait() == 1:
                        p.kill()
                        break
                    command = self.make_commands(syz_repro)
                    p3 = Popen(["ssh", "-p", str(self.ssh_port), "-F", "/dev/null", "-o", "UserKnownHostsFile=/dev/null", 
                    "-o", "BatchMode=yes", "-o", "IdentitiesOnly=yes", "-o", "StrictHostKeyChecking=no", 
                    "-o", "ConnectTimeout=10", "-i", "{}/stretch.id_rsa".format(self.image_path), 
                    "-v", "root@localhost", command],
                    stdout=PIPE,
                    stderr=STDOUT)
                    with p3.stdout:
                        self.__log_subprocess_output(p3.stdout, logging.INFO)
                    extract_report = True
                if extract_report:
                    if utilities.regx_match(boundary_regx, line) or \
                       utilities.regx_match(message_drop_regx, line):
                        record_flag ^= 1
                        if record_flag == 0 and kasan_flag == 1:
                            res.append(crash)
                            crash = []
                        continue
                    if utilities.regx_match(kasan_regx, line) or \
                       utilities.regx_match(free_regx, line):
                        kasan_flag ^= 1
                    if record_flag and kasan_flag:
                        crash.append(line)
        return res

    def make_commands(self, syz_repro):
        command = "./syz-execprog -executor=./syz-executor "
        enabled = "-enable="
        normal_pm = ["arch", "timeout", "procs", "threaded", "collide", "sandbox", "fault_call", "fault_nth", "os"]
        r = utilities.request_get(syz_repro)
        text = r.text.split('\n')
        for line in text:
            if line[0] == "#" and line[1] == "{":
                pm = {}
                try:
                    pm = json.loads(line[1:])
                except json.JSONDecodeError:
                    self.case_logger.info("Using old syz_repro")
                    pm = self.__convert_format(line[1:])
                for each in normal_pm:
                    if each in pm and pm[each] != "":
                        command += "-" + each + "=" +str(pm[each]) + " "
                if "repeat" in pm and pm["repeat"] != "":
                    if pm["repeat"] == 'true':
                        command += "-repeat=" + "0 "
                    else:
                        command += "-repeat=" + "1 "
                if "tun" in pm and pm["tun"] == "true":
                    enabled += "tun,"
                if "binfmt_misc" in pm and pm["binfmt_misc"] != "":
                    enabled += "binfmt_misc,"
                if "cgroups" in pm and pm["cgroups"] == "true":
                    enabled += "cgroups,"
                if "close_fds" in pm and pm["close_fds"] == "true":
                    enabled += "close_fds,"
                if "devlinkpci" in pm and pm["devlinkpci"] == "true":
                    enabled += "devlink_pci,"
                if "netdev" in pm and pm["netdev"] == "true":
                    enabled += "net_dev,"
                if "resetnet" in pm and pm["resetnet"] == "true":
                    enabled += "net_reset,"
                if "usb" in pm and pm["usb"] == "true":
                    enabled += "usb,"
                if enabled[-1] == ',':
                    command += enabled[:-1] + " testcase"
                else:
                    command += "testcase"
                break
        return command
    
    def monitor_execution(self, p):
        count = 0
        while (count < 30*60):
            count += 1
            time.sleep(1)
            poll = p.poll()
            if poll != None:
                return
        self.case_logger.info('Time out, kill qemu')
        p.kill()

    def __convert_format(self, line):
        res = {}
        p = re.compile(r'({| )(\w+):([0-9a-zA-Z-]*)')
        new_line = p.sub(r'\1"\2":"\3",', line)[:-2] + "}"
        pm = json.loads(new_line)
        for each in pm:
            if each == 'Threaded':
                res['threaded']=pm[each]
            if each == 'Collide':
                res['collide']=pm[each]
            if each == 'Repeat':
                res['repeat']=pm[each]
            if each == 'Procs':
                res['procs']=pm[each]
            if each == 'Sandbox':
                res['sandbox']=pm[each]
            if each == 'FaultCall':
                res['fault_call']=pm[each]
            if each == 'FaultNth':
                res['fault_nth']=pm[each]
            if each == 'EnableTun':
                res['tun']=pm[each]
            if each == 'EnableCgroups':
                res['cgroups']=pm[each]
            if each == 'UseTmpDir':
                res['tmpdir']=pm[each]
            if each == 'HandleSegv':
                res['segv']=pm[each]
            if each == 'Fault':
                res['fault']=pm[each]
            if each == 'WaitRepeat':
                res['wait_repeat']=pm[each]
            if each == 'Debug':
                res['debug']=pm[each]
            if each == 'Repro':
                res['repro']=pm[each]
        if len(pm) != len(res):
            self.logger.info("parameter is missing:\n%s\n%s", new_line, str(res))
        return res
            
    def __match_allocated_section(self, report1 ,report2):
        self.case_logger.info("match allocated section")
        allocation1 = self.__extract_allocated_section(report1)
        allocation2 = self.__extract_allocated_section(report2)
        seq1 = [self.__extract_func_name(x) for x in allocation1 if self.__extract_func_name(x) != None]
        seq2 = [self.__extract_func_name(x) for x in allocation2 if self.__extract_func_name(x) != None]
        
        diff = utilities.levenshtein_for_calltrace(seq1, seq2)
        ratio = diff/float(max(len(seq1), len(seq2)))
        self.case_logger.info("diff ratio: {}".format(ratio))
        if ratio > 0.3:
            return False
        return True
    
    def __match_call_trace(self, report1, report2):
        self.case_logger.info("match call trace")
        trace1 = self.__extrace_call_trace(report1)
        trace2 = self.__extrace_call_trace(report2)
        seq1 = [self.__extract_func_name(x) for x in trace1 if self.__extract_func_name(x) != None]
        seq2 = [self.__extract_func_name(x) for x in trace2 if self.__extract_func_name(x) != None]
        
        diff = utilities.levenshtein_for_calltrace(seq1, seq2)
        ratio = diff/float(max(len(seq1), len(seq2)))
        self.case_logger.info("diff ratio: {}".format(ratio))
        if ratio > 0.3:
            return False
        return True

    def __is_kasan_func(self, func_name):
        if func_name in self.kasan_func_list:
            return True
        return False
    
    def __extract_allocated_section(self, report):
        res = []
        record_flag = 0
        for line in report:
            if record_flag and not self.__is_kasan_func(self.__extract_func_name(line)):
                res.append(line)
            if utilities.regx_match(r'Allocated by task \d+', line):
                record_flag ^= 1
            if utilities.regx_match(r'Freed by task \d+', line):
                record_flag ^= 1
                break
        return res[:-2]
    
    def __extrace_call_trace(self, report):
        res = []
        record_flag = 0
        implicit_call_regx = r'\[.+\]  \?.*'
        for line in report:
            if record_flag and \
               not utilities.regx_match(implicit_call_regx, line) and \
               not self.__is_kasan_func(self.__extract_func_name(line)):
                res.append(line)
            if utilities.regx_match(r'Call Trace', line):
                record_flag ^= 1
            if record_flag == 1 and (utilities.regx_match(r'entry_SYSCALL', line) or\
                utilities.regx_match(r'Allocated by task', line)):
                record_flag ^= 1
                break
        return res

    def __extract_func_name(self, line):
        m = re.search(r'([A-Za-z0-9_.]+)\+0x[0-9a-f]+', line)
        if m != None and len(m.groups()) != 0:
            return m.groups()[0]
    
    def __init_case_logger(self, logger_name):
        handler = logging.FileHandler("{}/poc/log".format(self.case_path))
        format = logging.Formatter('%(asctime)s %(message)s')
        handler.setFormatter(format)
        logger = logging.getLogger(logger_name)
        logger.setLevel(self.logger.level)
        logger.addHandler(handler)
        return logger
    
    def __log_subprocess_output(self, pipe, log_level):
        for line in iter(pipe.readline, b''):
            line = line.decode("utf-8").strip('\n').strip('\r')
            if log_level == logging.INFO:
                self.case_logger.info(line)
            if log_level == logging.DEBUG:
                self.case_logger.debug(line)

def args_parse():
    parser = argparse.ArgumentParser(formatter_class=argparse.RawTextHelpFormatter,
                                     description='Determine if the new crashes are from the same root cause of the old one\n'
                                                 'eg. python crash.py -i 7fd1cbe3e1d2b3f0366d5026854ee5754d451405')
    parser.add_argument('-i', '--input', nargs='?', action='store',
                        help='By default it analyze all cases under folder \'succeed\', but you can indicate a specific one.')
    parser.add_argument('--ignore', nargs='?', action='store',
                        help='A file contains cases hashs which are ignored. One line for each hash.')
    args = parser.parse_args()
    return args

if __name__ == '__main__':
    args = args_parse()
    crawler = Crawler()

    logger = logging.getLogger('crash')
    hdlr = logging.FileHandler('./replay.out')
    formatter = logging.Formatter('%(asctime)s %(levelname)s %(message)s')
    hdlr.setFormatter(formatter)
    logger.addHandler(hdlr) 
    logger.setLevel(logging.INFO)

    ignore = []
    if args.ignore != None:
        with open(args.ignore, "r") as f:
            text = f.readlines()
            for line in text:
                line = line.strip('\n')
                ignore.append(line)

    path = "succeed"
    type = utilities.FOLDER
    if args.input != None:
        path = os.path.join(path, args.input[:7])
        type = utilities.CASE
    for url in utilities.urlsOfCases(path, type):
        if url not in ignore:
            crawler.run_one_case(url)
    
    count = 0
    for hash in crawler.cases:
        print("running case {} [{}/{}]".format(hash, count, len(crawler.cases)))
        project_path = os.getcwd()
        case_path = "{}/work/succeed/{}".format(project_path, hash[:7])
        case = crawler.cases[hash]
        syz_repro = case["syz_repro"]
        syz_commit = case["syzkaller"]
        commit = case["commit"]
        config = case["config"]
        log = case["log"]
        logger.info("Running case: {}".format(hash))
        checker = CrashChecker(project_path, case_path, default_port, logger)
        res = checker.run(syz_repro, syz_commit, log, commit, config)
        checker.logger.info("{}:{}".format(hash, res[0]))
        if res[0]:
            checker.logger.info("successful crash: {}".format(res[1]))
        count += 1