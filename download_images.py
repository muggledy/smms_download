from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.common import exceptions
from selenium.webdriver.support.wait import WebDriverWait
from selenium.webdriver.support import expected_conditions
from pathlib import Path
from itertools import count
from lxml import etree
from copy import deepcopy
from concurrent.futures import ThreadPoolExecutor, as_completed
import requests, time, random, pickle, os, re, yaml

def get_absolute_path(relative_path):
    absolute_path = \
        os.path.normpath(os.path.join(\
        os.path.dirname(__file__), relative_path))
    parent_dir = os.path.dirname(absolute_path)
    if not os.path.exists(parent_dir):
        print(f'Info: create dir {parent_dir}')
        os.makedirs(parent_dir)
    return absolute_path

def record_run_time(func):
    def wrapper(*args, **kwargs):
        start_time = time.time()
        ret = func(*args, **kwargs)
        print('Info: function [%s] run time is %.2fs' 
              % (func.__name__ , time.time()-start_time))
        return ret
    return wrapper

def login_smms_to_get_cookie(username, password):
    if not username or not password:
        print('Error: username or password is NULL(check user_config.yaml)')
        exit(1)
    options = webdriver.ChromeOptions()
    #避免程序退出后浏览器也随之关闭
    options.add_experimental_option('detach', True)
    options.add_argument('--start-maximized')
    driver = webdriver.Chrome(options=options)
    driver.get('https://sm.ms/login')
    try:
        username_input = driver.find_element(By.ID, 'username')
        password_input = driver.find_element(By.ID, 'password')
        login_button = driver.find_element(By.ID, 'submiButton')
    except exceptions.NoSuchElementException:
        print('Error: login page cannot find login button')
        exit(1)
    username_input.send_keys(username)
    password_input.send_keys(password)
    login_button.click()
    try:
        #等待条件：登陆成功会得到一个302响应，页面将重定向到https://sm.ms/，
        #并且会有一个"User"的下拉菜单（标签ID为"drop_user"）指向登录用户个人数据页面
        WebDriverWait(driver, 6).until((expected_conditions.url_to_be('https://sm.ms/') 
            and expected_conditions.presence_of_element_located((By.ID, 'drop_user'))))
    except Exception as e:
        print(f'Error: login failed for {e}')
        exit(1)
    cookie = driver.get_cookies()
    print(f'Info: login success and get cookie {cookie}')
    return cookie

def parse_picture_list_page(page_str):
    '''item in imgs_url_list is defined as:
       <filename_hash, filename, urls, kb_size, width_height, upload_date>'''
    #如果sm.ms网站改版，xpath可能会失效，需要重新解析
    try:
        html = etree.HTML(page_str, etree.HTMLParser())
        imgs_list, next_page = [], None
        pagination_ul = html.xpath("//nav/ul[@class='pagination']")
        pagination_li = pagination_ul[0].xpath('./li')
        curr_page_num_idx = [idx for idx, ele in enumerate(pagination_li) 
                            if ele.attrib.get('class', '') == 'active'][0]
        if curr_page_num_idx != (len(pagination_li) - 2):
            next_page = curr_page_num_idx + 1
        picture_items = html.xpath("//table[@id='table-picture']/tbody/tr")
        for pic in picture_items:
            a = pic.xpath('./td[2]/a')[0]
            href2, img_name = a.attrib.get('href'), a.text
            unique_hash_name = re.findall(r'^https://sm.ms/image/(.*)$', href2)[0]
            size = pic.xpath('./td[4]')[0].text
            witdh_height = pic.xpath('./td[position()=5 or position()=6]')
            witdh_height = tuple([int(_.text) for _ in witdh_height])
            upload_date = pic.xpath('./td[7]')[0].text
            href1 = pic.xpath('./td[8]/a[2]')[0].attrib.get('href')
            imgs_list.append((unique_hash_name, img_name, 
                            (href1, href2), size, witdh_height, upload_date))
        return (imgs_list, next_page)
    except Exception as e:
        print(f'Error: parse_picture_list_page failed for {e}')
        return None

def get_images_url_list_with_cookie(cookie, page=None):
    '''return(success): (current_page_url_list, next_page_num)
       return(fail): None'''
    if (page is not None) and (not isinstance(page, int)):
        print(f'Error: get_images_url_list_with_cookie param page {page} is invalid')
        return None
    rqt_rel_url = '/home/picture' if page is None else f'/home/picture?page={page}'
    rqt_url = f'https://sm.ms{rqt_rel_url}'
    headers = {
        'authority': 'sm.ms',
        'accept': 'text/html,application/xhtml+xml,application/xml;'
            'q=0.9,image/avif,image/webp,image/apng,*/*;'
            'q=0.8,application/signed-exchange;v=b3;q=0.7',
        'accept-language': 'zh-CN,zh;q=0.9',
        'cookie': '; '.join(sorted([f"{item.get('name')}={item.get('value')}" 
            for item in cookie if item.get('name') in ['smms', 'PHPSESSID']])),
        'sec-ch-ua': '"Not_A Brand";v="8", "Chromium";v="120", "Google Chrome";v="120"',
        'sec-ch-ua-mobile': '?0',
        'sec-ch-ua-platform': '"Windows"',
        'sec-fetch-dest': 'document',
        'sec-fetch-mode': 'navigate',
        'sec-fetch-site': 'none',
        'sec-fetch-user': '?1',
        'upgrade-insecure-requests': '1',
        'user-agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
            'AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    }
    time.sleep(random.random() * 3)
    try:
        req = requests.get(rqt_url, headers=headers, verify='./ISRG_Root_X2.pem')
        if req.ok:
            #如果服务器对get请求头验证失败（之前COOKIE构造错误，PHPSESSID:xxx，自纠数个小时），
            #也会返回http 200，但是是登陆页面，req.url为https://sm.ms/login
            if req.url != rqt_url:
                print(f'Error: get from {rqt_url} failed, return {req.url} page')
                print(f'Info: error request header is {req.request.headers}')
                return None
            print(f'Info: get {req.url} success({req.status_code})')
            req.encoding='utf-8'
            Path(get_absolute_path(f'./tmp/picture_list_page{1 if page is None else page}.html'))\
                .write_text(req.text)
            return parse_picture_list_page(req.text)
        else:
            print(f'Error: get from {rqt_url} failed for {req.reason}')
            return None
    except Exception as e:
        print(f'Error: get_images_url_list_with_cookie failed for {e}')
        return None

def smms_data_getter(cookie, get_data_method):
    def _wrapped(*args, **kwargs):
        return get_data_method(cookie, *args, **kwargs)
    return _wrapped

def is_expired_for_specified_days(anchor_timestamp, threshold_days):
    return int((time.time() - float(anchor_timestamp)) // (60 * 60 * 24)) > threshold_days

def get_newest_images_url_list_from_local(local_file_path):
    if not os.path.exists(local_file_path):
        print(f'Error: {local_file_path} not exist')
        return None
    with open(local_file_path, 'rb') as f:
        date_timestamp, data_imgs_url_list = pickle.load(f)
        date_timestamp = float(date_timestamp)
        #如果距离上次从SM.MS获取数据时间间隔超过一周，则重新获取并更新本地数据，否则直接加载本地保存的数据
        if not is_expired_for_specified_days(date_timestamp, 7):
            print(f'Info: get imgs_url_list(fetch from sm.ms at '
                f'{time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(date_timestamp))},'
                f' total {len(data_imgs_url_list)} pictures)'
                f' from local success')
            return data_imgs_url_list
        else:
            print('Info: local file has expired for more than one week, '
                  'must re-fetch from https://sm.ms')
    return None

@record_run_time
def get_newest_images_url_list(from_local=False):
    local_file_path = get_absolute_path('./tmp/newest_raw_images_url_list.pickle')
    #是否优先从本地获取个人图片资源列表
    if from_local:
        if os.path.exists(local_file_path):
            data = get_newest_images_url_list_from_local(local_file_path)
            if data is not None: return data
        else:
            print(f'Info: {local_file_path} not exist, must get images_url_list from https://sm.ms')
    user_config = yaml.load(Path(get_absolute_path('./user_config.yaml')).\
        read_text(), Loader=yaml.FullLoader)
    cookie = login_smms_to_get_cookie(user_config['username'], user_config['password'])
    get_img_url_list_with_page = smms_data_getter(cookie, get_images_url_list_with_cookie)
    all_imgs_url_list, next_page, pages_num = [], None, 0
    #连续get失败3次则退出
    fail_nums = 0
    #最大get循环次数100
    for i in count(1):
        if (fail_nums >= 3) or (i > 100):
            print('Error: get_img_url_list_with_page failed for 3 times '
                  'or exceed max iter_num 100')
            break
        ret = get_img_url_list_with_page(next_page)
        if ret is None:
            fail_nums += 1
            continue
        fail_nums, pages_num = 0, pages_num+1
        url_list, next_page = ret
        all_imgs_url_list.extend(url_list)
        if next_page is None:
            print(f'Info: no next page, all images_url_list pages fetched over, '
                  f'total {pages_num} pages/{len(all_imgs_url_list)} pictures')
            break
    with open(local_file_path, 'wb') as f:
        pickle.dump([str(time.time()), all_imgs_url_list], f, pickle.HIGHEST_PROTOCOL)
    return all_imgs_url_list

def compare_two_imgs_database(old_db, new_db):
    old_db = {k:tuple(v) for k,v in old_db.items()}
    new_db = {k:tuple(v) for k,v in new_db.items()}
    in_old_not_in_new = old_db.items() - new_db.items()
    in_new_not_in_old = new_db.items() - old_db.items()
    print('Info: compare old database(local) and new database(sm.ms)...')
    if (in_old_not_in_new == set()) and (in_new_not_in_old == set()):
        print('Info: no change between two databases')
        return False
    i1, i2, i3 = 0, 0, 0
    for k, v in in_old_not_in_new:
        if not new_db.get(k):
            print(f'{v[0]}({v[1][1]}) is deleted')
            i1 += 1
    for k, v in in_new_not_in_old:
        if old_db.get(k):
            print(f'{v[0]}({old_db.get(k)}->{v}) is updated') #won't be here
            i2 += 1
        else:
            print(f'{v[0]}({v[1][1]}) is added')
            i3 += 1
    print(f'Info: {i3} added, {i2} updated, {i1} deleted')
    return True

def update_imgs_resource_database(newest_raw_imgs_url_list):
    '''item in imgs_database(dict) is defined as:
       <filename_hash:[filename, urls, kb_size, width_height, upload_date, 
       download_flag, delete_flag, local_path]>'''
    local_file_path = get_absolute_path('./tmp/images_database.pickle')
    old_db = {}
    if not os.path.exists(local_file_path):
        print(f'Info: local images database({local_file_path}) not exist')
    else:
        with open(local_file_path, 'rb') as f:
            old_db = pickle.load(f)
            print(f'Info: get images database from local success'
                  f'(total {len(old_db)} pictures)')
    db = deepcopy(old_db)
    for k, v in db.items():
        v[6] = 1 #pre-set all delete_flag as 1
    for img_item in newest_raw_imgs_url_list:
        if db.get(img_item[0]) is None:
            db[img_item[0]] = [*img_item[1:], 0, 0, '']
        else:
            if list(db[img_item[0]][:5]) != list(img_item[1:]):
                print(f'Warn: {list(db[img_item[0]][:5])} '
                    f'updated to {list(img_item[1:])}???')
                db[img_item[0]] = [*img_item[1:], 0, 0, ''] #won't be here
            db[img_item[0]][6] = 0
    #比较旧数据库和新数据库，若无差别则直接return，否则更新到本地
    if not compare_two_imgs_database(old_db, db):
        print('Info: no need to update local imgs database')
        return old_db
    print('Info: save all update from sm.ms into local imgs database')
    with open(local_file_path, 'wb') as f:
        pickle.dump(db, f, pickle.HIGHEST_PROTOCOL)
    return db

def download_one_img(url, timeout = 6):
    headers = \
        {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'
        ' AppleWebKit/537.36 (KHTML, like Gecko) '
        'Chrome/51.0.2704.79 Safari/537.36 Edge/14.14393'}
    time.sleep(random.random() * 3) #needed?
    print(f'Info: download from {url}...')
    r = requests.get(url, headers=headers)
    if r.ok:
        return r.content
    return None

@record_run_time
def download_images_database_by_threadpool(imgs_database, max_workers = None):
    urls = {k:v[1][0] for k,v in imgs_database.items() if not v[5]}
    if not urls:
        print('Info: no pictures need to download')
        return imgs_database
    workers = ThreadPoolExecutor(max_workers=max_workers)
    future_download = {workers.submit(download_one_img, url, timeout=6):\
                       key for key,url in urls.items()}
    success_num = 0
    for task in as_completed(future_download):
        img_key = future_download[task]
        try:
            img_byte = task.result()
        except Exception as e:
            print(f'Error: download {imgs_database[img_key][1][0]} failed for {e}')
            continue
        if img_byte is None:
            print(f'Error: download {imgs_database[img_key][1][0]} failed for unknown reason')
            continue
        imgs_database[img_key][5] = 1
        local_path = \
            get_absolute_path(f'./img/{img_key}_{imgs_database[img_key][0]}')
        print(f'Info: save picture into {local_path}')
        Path(local_path).write_bytes(img_byte)
        imgs_database[img_key][-1] = local_path
        success_num += 1
    workers.shutdown(wait=True)
    print(f'Info: {success_num} download succeed, {len(urls) - success_num} failed')
    with open(get_absolute_path('./tmp/images_database.pickle'), 'wb') as f:
        pickle.dump(imgs_database, f, pickle.HIGHEST_PROTOCOL)
    return imgs_database

def render(template, context):
    '''https://github.com/muggledy/sim_badge/blob/master/api/badge_gen.py'''
    eval_patterns = re.findall('\{%%(.+?)%%\}', template)
    eval_patterns = set([re.sub(r' ', '', eval_p) for eval_p in eval_patterns])
    for eval_p in eval_patterns:
        val = str(eval(eval_p, context))
        eval_p = r'\{%% *' + \
        ' *'.join([f'\{_}' if _ in '.^*+|(){}[]' else _ for _ in \
            re.findall(r'(\w+|[^\w])', eval_p)]) + r' *%%\}'
        template = re.sub(eval_p, val, template)
    re_patterns = set(re.findall('\{% *(\w+?) *%\}', template))
    for re_p in re_patterns:
        val = str(context[re_p])
        re_p = r'\{% *' + re_p + r' *%\}'
        template = re.sub(re_p, val, template)
    return template

def export_imgs_database_into_html(imgs_database):
    export_html_template = Path(get_absolute_path(\
        './show_imgs_database_template.html')).\
        read_text(encoding='utf-8')
    tr_item_template, tr_body = '''<tr>
        <td>{% num %}</td>
        <td><a href="{% href %}" target="_blank">{% filename %}</a></td>
        <td style="padding:8px;">
          <img src="{% local_path %}" width="60" height="60"/></td>
        <td>{% kb_size %}</td>
        <td>{% width_height %}</td>
        <td>{% upload_date %}</td>
        <td>{% download_flag %}</td>
        <td>{% delete_flag %}</td>
        <td><a href="{% local_path %}" target="_blank">
        {% local_path %}</a></td>
    </tr>''', ''
    for i,(k,v) in enumerate(imgs_database.items(), 1):
        tr_body += render(tr_item_template, {'filename':v[0],\
            'href':v[1][1],'kb_size':v[2],'width_height':\
            ' x '.join([str(_) for _ in v[3]]),'upload_date':v[4],\
            'download_flag':('是' if v[5] else \
            '<font color="red">否</font>'),'delete_flag':\
            ('<font color="red">是</font>' if v[6] else '否'),'local_path':\
            v[7].replace("\\", "/") if v[7] else '', 'num':i})
    if not imgs_database:
        tr_body = '<tr><td colspan="8">暂无数据</td></tr>'
    html = render(export_html_template, {'tr_body':tr_body})
    print(f'Info: export imgs database(total '
          f'{len(imgs_database)} pictures) into export.html')
    Path(get_absolute_path('./export.html')).\
        write_text(html, encoding='utf-8')

if __name__ == '__main__':
    newest_imgs_url_list = get_newest_images_url_list(from_local=False)
    imgs_database = update_imgs_resource_database(newest_imgs_url_list)
    imgs_database = download_images_database_by_threadpool(imgs_database)
    export_imgs_database_into_html(imgs_database)
