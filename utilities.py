import os, re, stat
import requests
import numpy as np
import json
import datetime

from bs4 import BeautifulSoup
from syzbotCrawler import Crawler
from dateutil import parser as time_parser

FOLDER=0
CASE=1
URL=2

syzbot_bug_base_url = "bug?id="
syzbot_host_url = "https://syzkaller.appspot.com/"

def urlsOfCases(folder, type=FOLDER):
    res = []
    dirOfCases = "{}/work/{}".format(os.getcwd(), folder)
    paths = []

    if type == FOLDER:
        for dirs in os.listdir(dirOfCases):
            path = os.path.join(dirOfCases,dirs)
            paths.append(path)
    
    if type == CASE:
        paths.append(dirOfCases)
    
    for path in paths:
        for file in os.listdir(path):
            if file == "log":
                with open(os.path.join(path, file), "r") as f:
                    for line in f:
                        m = re.search(r'\[\d*\] https:\/\/syzkaller.appspot.com\/bug\?id=([a-z0-9]*)\n', line)
                        if m != None and len(m.groups()) != 0:
                            res.append(m.groups()[0])  
                            break
    
    return res

def regx_match(regx, line):
    m = re.search(regx, line)
    if m != None and len(m.group()) != 0:
        return True
    return False

def chmodX(path):
    st = os.stat(path)
    os.chmod(path, st.st_mode | stat.S_IEXEC)

def request_get(url):
    return requests.request(method='GET', url=url)

def levenshtein_for_calltrace(seq1, seq2):
    size_x = len(seq1) + 1
    size_y = len(seq2) + 1
    matrix = np.zeros ((size_x, size_y))
    for x in range(size_x):
        matrix [x, 0] = x
    for y in range(size_y):
        matrix [0, y] = y

    for x in range(1, size_x):
        for y in range(1, size_y):
            if seq1[x-1] == seq2[y-1]:
                matrix [x,y] = min(
                    matrix[x-1, y] + 1,
                    matrix[x-1, y-1],
                    matrix[x, y-1] + 1
                )
            else:
                matrix [x,y] = min(
                    matrix[x-1,y] + 1,
                    matrix[x-1,y-1] + 1,
                    matrix[x,y-1] + 1
                )
    #print (matrix)
    return (matrix[size_x - 1, size_y - 1])

def syzrepro_convert_format(line):
        res = {}
        p = re.compile(r'({| )(\w+):([0-9a-zA-Z-]*)')
        raw = p.sub(r'\1"\2":"\3",', line)
        new_line =raw[:raw.find('}')-1] + "}"
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
            if each == 'EnableCgroups' or each == 'Cgroups':
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
            if each == 'NetDevices':
                res['netdev']=pm[each]
            if each == 'NetReset':
                res['resetnet']=pm[each]
            if each == 'BinfmtMisc':
                res['binfmt_misc']=pm[each]
            if each == 'CloseFDs':
                res['close_fds']=pm[each]
            if each == 'DevlinkPCI':
                res['devlinkpci']=pm[each]
            if each == 'USB':
                res['usb']=pm[each]
        #if len(pm) != len(res):
        #    self.logger.info("parameter is missing:\n%s\n%s", new_line, str(res))
        return res

def use_and_free_same_task(url):
    res = None
    r = request_get(url)
    text = r.text.split('\n')
    use_flag = False
    free_flag = False
    for line in text:
        if regx_match(r'CPU: \d+ PID: \d+ Comm:', line):
            m = re.search(r'CPU: \d+ PID: (\d+) Comm:', line)
            task1 = m.groups()[0]
            use_flag = True
        if regx_match(r'Freed by task \d+', line):
            m = re.search(r'Freed by task (\d+):', line)
            task2 = m.groups()[0]
            free_flag = True
        """if use_flag:
            if regx_match(r'entry_SYSCALL', line):
                task1 = a1
                use_flag = False
            if regx_match(r'Allocated by task', line):
                use_flag = False
        if free_flag:
            if regx_match(r'entry_SYSCALL', line):
                task2 = a2
                free_flag = False
                break
            if regx_match(r'The buggy address belongs to the object at', line):
                free_flag = False
                break
        """
    try:
        if task1 == task2:
            res = True
        else:
            res = False
    except:
        print("Error: "+url)
    return res
        

#divide into race-condition and nonrace-condition
def get_types_of_cases(hashs):
    race = []
    non_race = []
    crawler = Crawler()
    for each in hashs:
        crawler.run_one_case(each)
    for hash in crawler.cases:
        case = crawler.cases[hash]
        syz_repro_url = case["syz_repro"]
        log_url = case["log"]
        r = request_get(syz_repro_url)
        text = r.text.split('\n')
        for line in text:
            if line.find('{') != -1 and line.find('}') != -1:
                pm = {}
                try:
                    pm = json.loads(line[1:])
                except json.JSONDecodeError:
                    pm = syzrepro_convert_format(line[1:])
                if 'repeat' in pm and str(pm['repeat']).lower() == 'true':
                    same_task = use_and_free_same_task(log_url)
                    if "threaded" in pm and str(pm["threaded"]).lower() == 'true':
                        if "collide" in pm and str(pm["collide"]).lower() == 'true':
                            race.append(hash)
                        else:
                            if 'procs' in pm and str(pm['procs'])!="1":
                                if same_task == False:
                                    race.append(hash)
                                else:
                                    non_race.append(hash)
                            else:
                                non_race.append(hash)
                    else:
                        if 'procs' in pm and str(pm['procs'])!="1":
                            if same_task == False:
                                race.append(hash)
                            else:
                                non_race.append(hash)
                        else:
                            non_race.append(hash)
                else:
                    non_race.append(hash)

                if "procs" in pm:
                    procs = pm["procs"]
                else:
                    procs = 1
                if "repeat" in pm:
                    repeat = pm["repeat"]
                else:
                    repeat = False
                if "threaded" in pm:
                    threaded = pm["threaded"]
                else:
                    threaded = False
                if "collide" in pm:
                    collide = pm["collide"]
                else:
                    collide = False
                if "repeat" in pm and str(pm["repeat"]).lower() == 'true':
                    print("race {} : procs:{} repeat:{} threaded:{} collide:{} same task:{}".format(hash, procs, repeat, threaded, collide, str(same_task)))
                
                break
    return [race, non_race]

def update_img_for_case(hash, folder, time):
    image_switching_date = datetime.datetime(2020, 3, 15)
    case_time = time_parser.parse(time)
    if image_switching_date <= case_time:
        image = "stretch"
    else:
        image = "wheezy"
    project_path = os.getcwd()
    src =  "{}/tools/img".format(project_path)
    des = "{}/work/{}/{}/img".format(project_path, folder, hash[:7])
    os.makedirs(des, exist_ok=True)
    os.symlink(os.path.join(src,image+".img"), os.path.join(des, "stretch.img"))
    os.symlink(os.path.join(src,image+".img.key"), os.path.join(des, "stretch.img.key"))

def get_case_from_file(path):
    res = []
    race = os.path.join(path,"race")
    non_race = os.path.join(path, "non-race")
    with open(race, 'r') as f:
        text = f.readlines()
        for line in text:
            line = line.strip('\n')
            res.append(line)
    with open(non_race, 'r') as f:
        text = f.readlines()
        for line in text:
            line = line.strip('\n')
            res.append(line)
    return res

def check_keyword_on_patch(hash):
    url = syzbot_host_url + syzbot_bug_base_url + hash
    req = request_get(url)
    soup = BeautifulSoup(req.text, "html.parser")
    try:
        fix = soup.body.span.contents[1]
        url = fix.attrs['href']
    except:
        res=None
    req = request_get(url)
    if regx_match(r' race(s)?( |-)| race(s)?\n| Race(s)?( |-)| Race(s)?\n', req.text):
        return True
    return False

if __name__ == '__main__':
    hashs = get_case_from_file('/tmp')
    for each in hashs:
        if check_keyword_on_patch(each):
            print("{}".format(each))
    """[race, nonrace] = get_types_of_cases(hashs)

    print("race")
    for each in race:
        print(each)
    print("non-race")
    for each in nonrace:
        print(each)"""
    