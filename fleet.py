from flask import Flask
from flask import send_file, render_template, flash, redirect, url_for, request, make_response, send_from_directory
from flask_cors import CORS, cross_origin
import os, sys, json, time, mimetypes, feedparser
from datetime import datetime, timedelta
from html.parser import HTMLParser
import urllib.request
#from flask_socketio import SocketIO
import random, string, tempfile
import sqlite3, re, hashlib
from pytz import timezone
from PIL import Image, ExifTags
from sendgrid import SendGridAPIClient
from sendgrid.helpers.mail import Mail
from random import randint
from twilio.rest import Client
from shutil import copyfile
from lxml import etree
from werkzeug.serving import WSGIRequestHandler

appdir = os.getcwd() + '/'

twilio_id = os.getenv('TWILIO_ID')
twilio_token = os.getenv('TWILIO_TOKEN')
sendgrid_token = os.getenv('SENDGRID_TOKEN')

app = Flask(__name__, static_url_path=appdir)
#socketio = SocketIO(app)
#socketio.init_app(app)
cookiedata = {'user':1}

myapp = appdir + os.getenv('FLEET_APP')
mydomain = os.getenv('APP_HOSTNAME')

UPLOAD_FOLDER = appdir + 'myfiles/'
ALLOWED_EXTENSIONS = set(['opml','txt', 'pdf', 'png', 'jpg', 'jpeg', 'gif'])
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER

@app.route('/file/<filename>')
def sendstatic(filename):
  return send_from_directory('public', filename)

def allowed_file(filename):
  return '.' in filename and \
    filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

@app.route('/')
def index():
  global fleet
  return fleet

def randomword(length):
   letters = string.ascii_lowercase
   return ''.join(random.choice(letters) for i in range(length))

def saveFile(field):
  if field in request.cookies:
    save = request.cookies[field]
    #del request.cookies[field]
    #setcookie(json.dumps(request.cookies))
    return save
  return ''

class FleetParser(HTMLParser):
  def __init__(self):
    HTMLParser.__init__(self)
    self.recording = 0
    self.data = []
  def handle_starttag(self, tag, attributes):
    if tag != 'script':
      return
    if self.recording:
      self.recording += 1
      return
    for name, value in attributes:
      if name == 'id' and value == 'server':
        break
    else:
      return
    self.recording = 1
  def handle_endtag(self, tag):
    if tag == 'script' and self.recording:
      self.recording -= 1
  def handle_data(self, data):
    if self.recording:
      self.data.append(data)

if not 'username' in vars() or 'username' in globals():
  username = ''

def dict_factory(cursor, row):
  d = {}
  for idx, col in enumerate(cursor.description):
    d[col[0]] = row[idx]
  return d

def add_one(resource,obj):
  resource = username + resource
  conn = sqlite3.connect('sqlite.db')
  cur = conn.cursor()
  cur.execute("""SELECT name FROM sqlite_master WHERE type='table' AND name=?;
    """,(resource,))
  if bool(cur.fetchone()) == False:
    sql = "CREATE TABLE " + resource + "(id INTEGER PRIMARY KEY"
    for key, val in obj.items():
      sql = sql + "," + key + " TEXT"
    sql = sql + ")"
    cur.execute(sql)
  sql = "pragma table_info(" + resource + ")"
  result = cur.execute(sql)
  fields = result.fetchall()
  exist = []
  for fie in fields:
    exist.append(fie[1])
  for field in obj.keys():
    if not field in exist:
      sql = "alter table " + resource + " add column " + field + " TEXT"
      cur.execute(sql)
  cols = ', '.join('"{}"'.format(col) for col in obj.keys())
  vals = ', '.join(':{}'.format(col) for col in obj.keys())
  sql = 'INSERT INTO "{0}" ({1}) VALUES ({2})'.format(resource, cols, vals)
  cur = conn.cursor()
  cur.execute(sql, obj)
  conn.commit()
  rid = cur.lastrowid
  conn.close()
  return rid

def get_one_by(resource,value,field):
  resource = username + resource
  conn = sqlite3.connect('sqlite.db')
  conn.row_factory = dict_factory
  cur = conn.cursor()
  vals = []
  sql = "SELECT * FROM " + resource
  sql = sql + " WHERE " + field + " like ?"
  vals.append(value)
  try:
    result = conn.cursor().execute(sql,vals)
  except sqlite3.OperationalError:
    return []
  return result.fetchall()

def get_one(resource,id):
  resource = username + resource
  conn = sqlite3.connect('sqlite.db')
  conn.row_factory = dict_factory
  cur = conn.cursor()
  cur.execute("""SELECT name FROM sqlite_master WHERE type='table' AND name=?;
    """,(resource,))
  if bool(cur.fetchone()) == False:
    return []
  sql = "SELECT * FROM " + resource + " WHERE id = ?"
  try:
    result = conn.cursor().execute(sql,[id])
  except sqlite3.OperationalError:
    return []
  return result.fetchall()

def mod_one(resource,obj,id):
  resource = username + resource
  conn = sqlite3.connect('sqlite.db')
  cur = conn.cursor()
  sql = "UPDATE " + resource + " SET "
  comma = ""
  vals = []
  for key, val in obj.items():
    sql = sql + comma + key + "=?"
    vals.append(val)
    comma = ","
  sql = sql + " WHERE id = ?"
  vals.append(id)
  conn.cursor().execute(sql,vals)
  return conn.commit()

def del_one(resource,id):
  resource = username + resource
  conn = sqlite3.connect('sqlite.db')
  cur = conn.cursor()
  sql = "DELETE FROM " + resource + " WHERE id = ?"
  conn.cursor().execute(sql,[id])
  return conn.commit()

def get_all(resource):
  resource = username + resource
  conn = sqlite3.connect('sqlite.db')
  cur = conn.cursor()
  cur.execute("""SELECT name FROM sqlite_master WHERE type='table' AND name=?;
    """,(resource,))
  if bool(cur.fetchone()) == False:
    return []
  conn.row_factory = dict_factory
  try:
    result = conn.cursor().execute("SELECT * FROM " + resource + ' ORDER BY id DESC')
  except sqlite3.OperationalError:
    return []
  return result.fetchall()

def sendcomet(recips,obj):
  for id in recips:
    recip = get_parent(id)
    print('SEND ' + '/rply/' + recip['code'],file=sys.stderr)
    #socketio.emit('new item', obj, namespace='/rply/' + recip['code'] )
  return True

def get_parent(id):
  id = int(id)
  conn = sqlite3.connect('sqlite.db')
  conn.row_factory = dict_factory
  cur = conn.cursor()
  sql = "SELECT * FROM contacts WHERE id = ?"
  result = conn.cursor().execute(sql,[id])
  rows = result.fetchall()
  if len(rows) > 0:
    if len(rows[0]['email']) > 0:
      items = get_one_by( 'contacts', rows[0]['email'], 'email' )
      if len(items) > 0:
        for con in items:
          if con['user_id'] is None:
            return con
    if len(rows[0]['phone']) > 0:
      items = get_one_by( 'contacts', rows[0]['phone'], 'phone' )
      if len(items) > 0:
        for con in items:
          if con['user_id'] is None:
            return con
  return rows[0]

def notify_photo(id):
  num = randint(100, 999)
  cont = get_user(id)
  if len(cont['phone']) > 0:
    client = Client(twilio_id, twilio_token)
    rl = current_user()['name'] + " shared a post with you " + 'https://' + mydomain + '/#/connect/' + cont['code']
    message = client.messages.create(
      body=rl,
      from_='+19718036380',
      to=cont['phone']
    )
    print('notify ' + cont['phone'], file=sys.stderr)
  if len(cont['email']) > 0:
    owner = current_user()
    em = cont['email']
    message = Mail(
      from_email=owner['name'] + ' via ' + mydomain + ' <brian@hoverkitty.com>',
      to_emails=em,
      subject=current_user()['name'] + " shared a post ",
      html_content='<strong><p>' + current_user()['name'] + " shared a post with you on " + mydomain + " </p><a href=\"" + 'https://' + mydomain + '/#/connect/' + cont['code'] + '">' + owner['name'] + "'s post" + '</a></strong>')
    sg = SendGridAPIClient(sendgrid_token)
    response = sg.send(message)
    print('notify ' + em, file=sys.stderr)
  return True

def get_user(id):
  resource = username + 'contacts'
  id = int(id)
  conn = sqlite3.connect('sqlite.db')
  conn.row_factory = dict_factory
  cur = conn.cursor()
  sql = "SELECT * FROM " + resource + " WHERE id = ?"
  try:
    result = conn.cursor().execute(sql,[id])
  except sqlite3.OperationalError:
    return []
  return result.fetchall()[0]

def current_user():
  rows = []
  if 'user' in request.cookies:
    id = request.cookies['user']
    conn = sqlite3.connect('sqlite.db')
    conn.row_factory = dict_factory
    cur = conn.cursor()
    sql = "SELECT * FROM contacts WHERE id = ?"
    result = conn.cursor().execute(sql,[id])
    rows = result.fetchall()
  if len(rows) > 0:
    if len(rows[0]['email']) > 0:
      items = get_one_by( 'contacts', rows[0]['email'], 'email' )
      if len(items) > 0:
        for con in items:
          rows[0]['groups'] = json.dumps(json.loads(rows[0]['groups']) + json.loads(con['groups']))
    if len(rows[0]['phone']) > 0:
      items = get_one_by( 'contacts', rows[0]['phone'], 'phone' )
      if len(items) > 0:
        for con in items:
          rows[0]['groups'] = json.dumps(json.loads(rows[0]['groups']) + json.loads(con['groups']))
    rows[0]['groups'] = json.dumps(list(set(json.loads(rows[0]['groups']))))
    return rows[0]
  return {'user_id':0,'groups':'[0]','code':'xoxo2020'}

def can(action,resource,user,obj=False):
  if user['groups'] is None:
    return False
  groups = json.loads(user['groups'])
  for grp in groups:
    for abb in all_abilities:
      if abb['group_id'] == grp and abb['resource'] == resource:
        if action in abb['actions']:
          if 'conditions' in abb:
            for cond in abb['conditions']:
              if cond[0] in obj:
                if isinstance(obj[cond[0]],str):
                  if isinstance(user[cond[1]],int):
                    if user[cond[1]] == int(obj[cond[0]]):
                      return True
                if isinstance(obj[cond[0]],int):
                  if isinstance(user[cond[1]],int):
                    if user[cond[1]] == obj[cond[0]]:
                      return True
                if not obj[cond[0]] is None:
                  needle = json.loads(obj[cond[0]])
                  if isinstance(needle,list):
                    haystack = json.loads(user[cond[1]])
                    if isinstance(haystack,list):
                      for findit in needle:
                        if findit in haystack:
                          return True
            return False
          else:
            return True
  return False

def randomword(length):
   letters = string.ascii_lowercase
   return ''.join(random.choice(letters) for i in range(length))

@app.route('/reportit',methods=['GET'])
def reportit():
  html = '<table>'
  html = html + '<tr><td>Name</td><td>ID</td><td>Groups</td><td>User ID</td><td>Phone</td><td>Email</td><td>code</td></tr>'
  for gg in get_all('contacts'):
    if not gg['groups'] is None:
      html = html + '<tr><td>' + gg['name'] + '</td><td>' + str(gg['id']) + '</td><td>' + json.dumps(gg['groups']) + '</td><td>' + str(gg['user_id']) + '</td><td>' + gg['phone']+ '</td><td>' + gg['email'] + '</td><td>' + str(gg['code']) + '</td></tr>'
  html = html + '</table>'
  html = html + '<hr><table>'
  html = html + '<tr><td>Name</td><td>ID</td><td>User ID</td></tr>'
  for gg in get_all('groups'):
    html = html + '<tr><td>' + gg['name'] + '</td><td>' + str(gg['id']) + '</td><td>' + str(gg['user_id']) + '</td></tr>'
  html = html + '</table>'
  html = html + '<hr><table>'
  html = html + '<tr><td>Title</td><td>ID</td><td>Groups</td><td>User ID</td></tr>'
  for gg in get_all('posts'):
    html = html + '<tr><td>' + gg['title'] + '</td><td>' + str(gg['id']) + '</td><td>' + json.dumps(gg['groups']) + '</td><td>' + str(gg['user_id']) + '</td></tr>'
  html = html + '</table>'
  return html

def update_abilities():
  global all_abilities, abilities
  all_abilities = abilities
  recs = get_all('groups')
  for item in recs:
    all_abilities.append({
      'group_id':item['id'],
      'resource':'posts',
      'actions':['add']
    })
    all_abilities.append({
      'group_id':item['id'],
      'resource':'posts',
      'actions':['get'],
      'conditions':[['groups','groups']]
    })
    all_abilities.append({
      'group_id':item['id'],
      'resource':'posts',
      'actions':['mod','del'],
      'conditions':[['user_id','id']]
    })

fleet = open(myapp).read()

par = FleetParser()
par.feed(fleet)
par.close()
server = 'global abilities' + "\n" + ''.join(par.data)

server = server.replace('rp.ly',mydomain)
fleet = fleet.replace('rp.ly',mydomain)

if not os.path.exists('sqlite.db'):
  newrec = {
    'name' : 'Group 1',
    'user_id' : 0,
    'created' : str( timezone( 'US/Pacific' ).localize( datetime.now() ) )
  }
  gr1 = add_one( 'groups', newrec )
  newrec = {
    'name' : 'Group 2',
    'user_id' : 0,
    'created' : str( timezone( 'US/Pacific' ).localize( datetime.now() ) )
  }
  gr2 = add_one( 'groups', newrec )
  newrec = {
    'name' : 'Group 1',
    'user_id' : 0,
    'created' : str( timezone( 'US/Pacific' ).localize( datetime.now() ) )
  }
  u = add_one('contacts',{
    'name':'',
    'email':'',
    'phone':'',
    'groups':json.dumps([2]),
    'code':'',
    'user_id':0,
    'created':str(datetime.now())
  })
  print('CREATED DB')

abilities = []
all_abilities = []

exec(server,globals(),{'app':app,'request':request,'abilities':abilities})

update_abilities()

#WSGIRequestHandler.protocol_version = "HTTP/1.1"


# export APP_HOSTNAME=photo.gy
# export FLEET_APP=rp.ly.html
# gunicorn --certfile=fullchain.pem --keyfile=privkey.pem --bind 165.227.57.132:443 --log-file=fleet.log fleet:app
if not sys.argv[0] == 'fleet.py':
  if __name__ == "__main__":
    app.run()

# export APP_HOSTNAME=photo.gy
# export FLEET_APP=rp.ly.html
# python3 fleet.py
if sys.argv[0] == 'fleet.py':
  if __name__ == "__main__":
    app.run(host=sys.argv[1],port=443,ssl_context=('fullchain.pem', 'privkey.pem'))


