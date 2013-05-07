import ConfigParser
from datetime import datetime
from functools import wraps
import logging
import os
import re
import sys

import envoy
import git

from DDR import CONFIG_FILE
from DDR import storage
from DDR.models import Collection, Entity
from DDR.changelog import write_changelog_entry
from DDR.control import CollectionControlFile, EntityControlFile
from DDR.xml import EAD, METS


class NoConfigError(Exception):
    def __init__(self, value):
        self.value = value
    def __str__(self):
        return repr(self.value)

if not os.path.exists(CONFIG_FILE):
    raise NoConfigError('No config file!')
config = ConfigParser.ConfigParser()
config.read(CONFIG_FILE)
GITOLITE = config.get('workbench','gitolite')
GIT_REMOTE_NAME = config.get('workbench','remote')


MODULE_PATH = os.path.dirname(os.path.abspath(__file__))
TEMPLATE_PATH = os.path.join(MODULE_PATH, 'templates')
GITIGNORE_TEMPLATE = os.path.join(TEMPLATE_PATH, 'gitignore.tpl')


def collection_git_url(collection_uid):
    return '{}:{}'.format(GITOLITE, collection_uid)

def annex_whereis_file(repo, file_path_rel):
    """Show remotes that the file appears in
    
    $ git annex whereis files/ddr-testing-201303051120-1/files/20121205.jpg
    whereis files/ddr-testing-201303051120-1/files/20121205.jpg (2 copies)
            0bbf5638-85c9-11e2-aefc-3f0e9a230915 -- workbench
            c1b41078-85c9-11e2-bad2-17e365f14d89 -- here
    ok
    
    @param repo: A GitPython Repo object
    @param collection_uid: A valid DDR collection UID
    @return: List of names of remote repositories.
    """
    remotes = []
    stdout = repo.git.annex('whereis', file_path_rel)
    logging.debug('\n{}'.format(stdout))
    lines = stdout.split('\n')
    if ('whereis' in lines[0]) and ('ok' in lines[-1]):
        num_copies = int(lines[0].split(' ')[2].replace('(',''))
        logging.debug('    {} copies'.format(num_copies))
        remotes = [line.split('--')[1].strip() for line in lines[1:-1]]
        logging.debug('    remotes: {}'.format(remotes))
    return remotes

def gitolite_connect_ok():
    """See if we can connect to gitolite server.
    
    We should do some lightweight operation, just enough to make sure we can connect.
    But we can't ping.
    
    http://gitolite.com/gitolite/user.html#info
    "The only command that is always available to every user is the info command
    (run ssh git@host info -h for help), which tells you what version of gitolite
    and git are on the server, and what repositories you have access to. The list
    of repos is very useful if you have doubts about the spelling of some new repo
    that you know was setup."
    Sample output:
        hello gjost, this is git@mits running gitolite3 v3.2-19-gb9bbb78 on git 1.7.2.5
        
         R W C  ddr-densho-[0-9]+
         R W C  ddr-densho-[0-9]+-[0-9]+
         R W C  ddr-dev-[0-9]+
        ...
    
    @return: True or False
    """
    logging.debug('    DDR.commands.gitolite_connect_ok()')
    cmd = 'ssh {} info'.format(GITOLITE)
    logging.debug('        {}'.format(cmd))
    r = envoy.run(cmd, timeout=30)
    logging.debug('        {}'.format(r.status_code))
    if r.status_code == 0:
        lines = r.std_out.split('\n')
        if len(lines) and ('this is {} running gitolite'.format(GITOLITE) in lines[0]):
            logging.debug('        OK ')
            return True
    logging.debug('        NO CONNECTION')
    return False

def requires_network(f):
    """Indicate that function requires network access; check if can connect to gitolite server.
    """
    @wraps(f)
    def wrapper(*args, **kwargs):
        if not gitolite_connect_ok():
            logging.error('Cannot connect to git server {}'.format(GITOLITE))
            return 1,'cannot connect to git server {}'.format(GITOLITE)
        return f(*args, **kwargs)
    return wrapper

def local_only(f):
    """Indicate that function requires no network access.
    """
    @wraps(f)
    def wrapper(*args, **kwargs):
        return f(*args, **kwargs)
    return wrapper

def command(f):
    """Indicate that function is a command-line command.
    """
    @wraps(f)
    def wrapper(*args, **kwargs):
        logging.debug('------------------------------------------------------------------------')
        logging.debug('{}.{}({}, {})'.format(f.__module__, f.__name__, args, kwargs))
        return f(*args, **kwargs)
    return wrapper

def list_staged(repo):
    """Returns list of currently staged files
    
    Works for git-annex files just like for regular files.
    
    @param repo: A Gitpython Repo object
    @return: List of filenames
    """
    return repo.git.diff('--cached', '--name-only').split('\n')

def list_committed(repo, commit):
    """Returns list of all files in the commit

    $ git log -1 --stat 0a1b2c3d4e...|grep \|

    @param repo: A Gitpython Repo object
    @param commit: A Gitpython Commit object
    @return: list of filenames
    """
    # return just the files from the specific commit's log entry
    entry = repo.git.log('-1', '--stat', commit.hexsha).split('\n')
    entrylines = [line for line in entry if '|' in line]
    files = [line.split('|')[0].strip() for line in entrylines]
    return files



description="""Various commands for manipulating DDR collections and entities."""

epilog="""
More than you thought you wanted to know about the collection command.
"""

OPERATIONS = [
    'clocal',
    'create',
    'clone',
    'destroy',
    'status',
    'astatus',
    'update',
    'sync',
    'ecreate',
    'edestroy',
    'eupdate',
    'eadd',
    'pull',
    'push',
    ]



def commit_files(repo, message, regular_files=[], annex_files=[]):
    """git-add and git-annex-add files and commit them
    
    @param repo: GitPython Repo object
    @param message: String
    @param regular_files: List of filenames relative to repo root.
    @param annex_files: List of filenames relative to repo root.
    @return: GitPython Repo object
    """
    added = annex_files + regular_files
    added.sort()
    logging.debug('    files added:         {}'.format(added))
    
    if annex_files:
        repo.git.annex('add', annex_files)
    if regular_files:
        repo.index.add(regular_files)
    
    staged = list_staged(repo)
    staged.sort()
    logging.debug('    files staged:        {}'.format(staged))
    logging.debug('    all files staged:    {}'.format(added == staged))
    # TODO cancel commit if list of staged doesn't match list of files added?
    
    commit = repo.index.commit(message)
    logging.debug('    commit: {}'.format(commit.hexsha))
    
    committed = list_committed(repo, commit)
    committed.sort()
    logging.debug('    files committed:     {}'.format(committed))
    logging.debug('    all files committed: {}'.format(added == staged == committed))
    # TODO complain if list of committed files doesn't match lists of added and staged files?
    
    return repo



@command
@local_only
def removables():
    return 0,storage.removables()

@command
@local_only
def removables_mounted():
    return 0,storage.removables_mounted()

@command
@local_only
def mount( device_file, label ):
    """Command-line function for mounting specified device on local system.
    """
    return 0,storage.mount(device_file, label)

@command
@local_only
def umount( device_file ):
    """Command-line function for UNmounting specified device on local system.
    """
    return 0,storage.umount(device_file)

@command
@local_only
def remount( device_file, label ):
    """Command-line function for unmounting and remounting specified device on local system.
    """
    return 0,storage.remount(device_file, label)

@command
@local_only
def mount_point( path ):
    return 0,storage.mount_point(path)

@command
@local_only
def storage_status( path ):
    return 0,storage.storage_status(path)



@command
@local_only
def collections_local(collections_root, repository, organization):
    """Command-line function for listing collections on the local system.
    
    Looks for directories under collection_root with names matching the
    "{repository}-{organization}-*" pattern and containing ead.xml files.
    Doesn't check validity beyond that.
    
    @param collections_root: Absolute path of dir in which collections are located.
    @param repository: Repository keyword.
    @param organization: Organization keyword.
    @return: list of collection UIDs
    """
    collections = []
    if os.path.exists(collections_root) and os.path.isdir(collections_root):
        regex = '^{}-{}-[0-9]+$'.format(repository, organization)
        logging.debug('    {}'.format(regex))
        uid = re.compile(regex)
        for x in os.listdir(collections_root):
            m = uid.search(x)
            if m:
                colldir = os.path.join(collections_root,x)
                if 'ead.xml' in os.listdir(colldir):
                    collections.append(colldir)
        collections.sort()
    else:
        logging.error('    {} does not exist or is not a directory'.format(collections_root))
    return collections


@command
@requires_network
def clone(user_name, user_mail, collection_uid, alt_collection_path):
    """Command-line function for cloning an existing collection.
    
    Clones existing collection object from workbench server.
    
    @param user_name: Username for use in changelog, git log
    @param user_mail: User email address for use in changelog, git log
    @param collection_uid: A valid DDR collection UID
    @param alt_collection_path: Absolute path to which repo will be cloned (includes collection UID)
    @return: message ('ok' if successful)
    """
    url = '{}:{}.git'.format(GITOLITE, collection_uid)
    
    repo = git.Repo.clone_from(url, alt_collection_path)
    logging.debug('    git clone {}'.format(url))
    if repo:
        logging.debug('    OK')
    else:
        logging.error('    COULD NOT CLONE!')
        return 1,'could not clone'
    if os.path.exists(os.path.join(alt_collection_path, '.git')):
        logging.debug('    .git/ is present')
    else:
        logging.error('    .git/ IS MISSING!')
        return 1,'.git/ is missing'
    # git annex init if not already existing
    if not os.path.exists(os.path.join(alt_collection_path, '.git', 'annex')):
        logging.debug('    git annex init')
        repo.git.annex('init')
    #
    repo.git.checkout('master')
    repo.git.config('user.name', user_name)
    repo.git.config('user.email', user_mail)
    repo.git.config('annex.sshcaching', 'false')
    if not GIT_REMOTE_NAME in [r.name for r in repo.remotes]:
        repo.create_remote(GIT_REMOTE_NAME, collection_git_url(collection_uid))
    return 0,'ok'


@command
@requires_network
def create(user_name, user_mail, collection_path):
    """Command-line function for creating a new collection.
    
    Clones a blank collection object from workbench server, adds files, commits.
    
    - clones new repo from gitolite server
    # Easier to have Gitolite create repo then clone (http://sitaramc.github.com/gitolite/repos.html)
    # than to add existing to Gitolite (http://sitaramc.github.com/gitolite/rare.html#existing).
    local requests CID from workbench API
    background:collection init: $ collection -cCID -oinit]
    background:collection init: $ git clone git@mits:ddr-ORG-C
        $ git clone git@mits:ddr-densho-1
        Cloning into 'ddr-densho-1'...
        Initialized empty Git repository in /home/git/repositories/ddr-densho-1.git/
        warning: You appear to have cloned an empty repository.
    background:entity init: $ git annex init
    background:entity init: $ git add changelog control ead.xml .gitignore
    background:entity init: $ git commit
    
    @param user_name: Username for use in changelog, git log
    @param user_mail: User email address for use in changelog, git log
    @param collection_path: Absolute path to collection repo.
    @return: message ('ok' if successful)
    """
    collection_uid = os.path.basename(collection_path)
    url = '{}:{}.git'.format(GITOLITE, collection_uid)
    
    repo = git.Repo.clone_from(url, collection_path)
    logging.debug('    git clone {}'.format(url))
    if repo:
        logging.debug('    OK')
    else:
        logging.error('    COULD NOT CLONE!')
    if os.path.exists(os.path.join(collection_path, '.git')):
        logging.debug('    .git/ is present')
    else:
        logging.error('    .git/ IS MISSING!')
    # there is no master branch at this point
    repo.create_remote(GIT_REMOTE_NAME, collection_git_url(collection_uid))
    repo.git.config('user.name', user_name)
    repo.git.config('user.email', user_mail)
    repo.git.config('gitweb.owner', '{} <{}>'.format(user_name, user_mail))
    repo.git.config('annex.sshcaching', 'false')
    git_files = []

    # add files
    # control
    control_path_rel = 'control'
    control_path_abs = os.path.join(collection_path, control_path_rel)
    CollectionControlFile.create(control_path_abs, collection_uid)
    if os.path.exists(control_path_abs):
        git_files.append(control_path_rel)
    else:
        logging.error('    COULD NOT CREATE control')
    # ead.xml
    ead_path_rel = 'ead.xml'
    ead_path_abs = os.path.join(collection_path, ead_path_rel)
    EAD.create(ead_path_abs)
    if os.path.exists(ead_path_abs):
        git_files.append(ead_path_rel)
    else:
        logging.error('    COULD NOT CREATE ead')
    # changelog
    changelog_path_rel = 'changelog'
    changelog_path_abs = os.path.join(collection_path, changelog_path_rel)
    changelog_messages = ['Initialized collection {}'.format(collection_uid)]
    write_changelog_entry(changelog_path_abs, changelog_messages, user_name, user_mail)
    if os.path.exists(changelog_path_abs):
        git_files.append(changelog_path_rel)
    else:
        logging.error('    COULD NOT CREATE changelog')
    # .gitignore
    gitignore_path_rel = '.gitignore'
    gitignore_path_abs = os.path.join(collection_path, gitignore_path_rel)
    with open(GITIGNORE_TEMPLATE, 'r') as f:
        gitignore_template = f.read()
    with open(gitignore_path_abs, 'w') as gitignore:
        gitignore.write(gitignore_template)
    if os.path.exists(gitignore_path_abs):
        git_files.append(gitignore_path_rel)
    else:
        logging.error('    COULD NOT CREATE .gitignore')
    # add files and commit
    repo = commit_files(repo, changelog_messages[0], git_files, [])
    # master branch should be created by this point
    # git annex init
    logging.debug('    git annex init')
    repo.git.annex('init')
    if os.path.exists(os.path.join(collection_path, '.git', 'annex')):
        logging.debug('    .git/annex/ OK')
    else:
        logging.error('    .git/annex/ IS MISSING!')
    
    # this little dance is necessary for some reason -- see notes
    logging.debug('    pushing master')
    repo.git.push('origin', 'master')
    logging.debug('    OK')
    repo.git.checkout('git-annex')
    logging.debug('    pushing git-annex')
    repo.git.push('origin', 'git-annex')
    logging.debug('    OK')
    repo.git.checkout('master')
    return 0,'ok'


@command
@local_only
def destroy():
    """Command-line function for removing  an entire collection's files from the local system.
    
    Does not remove files from the server!  That will remain a manual operation.
    
    @return: message ('ok' if successful)
    """
    return 1,'not implemented yet'


@command
@local_only
def status(collection_path):
    """Command-line function for running git status on collection repository.
    
    @param collection_path: Absolute path to collection repo.
    @return: message ('ok' if successful)
    """
    repo = git.Repo(collection_path)
    status = repo.git.status()
    logging.debug('\n{}'.format(status))
    return 0,status


@command
@requires_network
def annex_status(collection_path):
    """Command-line function for running git annex status on collection repository.
    
    @param collection_path: Absolute path to collection repo.
    @return: message ('ok' if successful)
    """
    repo = git.Repo(collection_path)
    status = repo.git.annex('status')
    logging.debug('\n{}'.format(status))
    return 0,status


@command
@local_only
def update(user_name, user_mail, collection_path, updated_files):
    """Command-line function for commiting changes to the specified file.
    
    NOTE: Does not push to the workbench server.
    @param user_name: Username for use in changelog, git log
    @param user_mail: User email address for use in changelog, git log
    @param collection_path: Absolute path to collection repo.
    @param updated_files: List of relative paths to updated file(s).
    @return: message ('ok' if successful)
    """
    collection_uid = os.path.basename(collection_path)
    repo = git.Repo(collection_path)
    if repo:
        logging.debug('    git repo {}'.format(collection_path))
    repo.git.checkout('master')
    repo.git.config('user.name', user_name)
    repo.git.config('user.email', user_mail)
    repo.git.config('annex.sshcaching', 'false')
    if not GIT_REMOTE_NAME in [r.name for r in repo.remotes]:
        repo.create_remote(GIT_REMOTE_NAME, collection_git_url(collection_uid))
    
    # changelog
    changelog_path_rel = 'changelog'
    changelog_path_abs = os.path.join(collection_path, changelog_path_rel)
    changelog_messages = []
    for f in updated_files:
        changelog_messages.append('Updated collection file(s) {}'.format(f))
    write_changelog_entry(
        changelog_path_abs,
        changelog_messages,
        user_name, user_mail)
    if os.path.exists(changelog_path_abs):
        updated_files.append(changelog_path_abs)
    else:
        logging.error('    COULD NOT UPDATE changelog')
    # add files and commit
    repo = commit_files(repo, 'Updated metadata files', updated_files, [])
    return 0,'ok'


@command
@requires_network
def sync(user_name, user_mail, collection_path):
    """Command-line function for git pull/push to workbench server, git-annex sync
    
    Pulls changes from and pushes changes to the workbench server.

    For this to work properly with Gitolite, it's necessary to push/pull
    on both the master AND git-annex branches.
    Sequence:
    - fetch
    - pull on master,git-annex branches
    - push on git-annex,master branches
    
    TODO This assumes that origin is the workbench server...
    
    @param user_name: Username for use in changelog, git log
    @param user_mail: User email address for use in changelog, git log
    @param collection_path: Absolute path to collection repo.
    @return: message ('ok' if successful)
    """
    repo = git.Repo(collection_path)
    repo.git.checkout('master')
    repo.git.config('user.name', user_name)
    repo.git.config('user.email', user_mail)
    repo.git.config('annex.sshcaching', 'false')
    if not GIT_REMOTE_NAME in [r.name for r in repo.remotes]:
        repo.create_remote(GIT_REMOTE_NAME, collection_git_url(collection_uid))
    # fetch
    repo.git.fetch('origin')
    # pull on master,git-annex branches 
    logging.debug('    git pull origin master')
    repo.git.checkout('master')
    repo.git.pull('origin', 'master')
    logging.debug('    OK')
    logging.debug('    git pull origin git-annex')
    repo.git.checkout('git-annex')
    repo.git.pull('origin', 'git-annex')
    logging.debug('    OK')
    # push on git-annex,master branches
    logging.debug('    git push origin git-annex')
    repo.git.checkout('git-annex')
    repo.git.push('origin', 'git-annex')
    logging.debug('    OK')
    logging.debug('    git push origin master')
    repo.git.checkout('master')
    repo.git.push('origin', 'master')
    logging.debug('    OK')
    # git annex sync
    logging.debug('    git annex sync')
    repo.git.annex('sync')
    logging.debug('    OK')
    return 0,'ok'


@command
@local_only
def entity_create(user_name, user_mail, collection_path, entity_uid):
    """Command-line function for creating an entity and adding it to the collection.
    
    @param user_name: Username for use in changelog, git log
    @param user_mail: User email address for use in changelog, git log
    @param collection_path: Absolute path to collection repo.
    @param entity_uid: A valid DDR entity UID
    @return: message ('ok' if successful)
    """
    collection = Collection(collection_path)
    repo = git.Repo(collection_path)
    repo.git.checkout('master')
    repo.git.config('user.name', user_name)
    repo.git.config('user.email', user_mail)
    repo.git.config('annex.sshcaching', 'false')
    if not GIT_REMOTE_NAME in [r.name for r in repo.remotes]:
        repo.create_remote(GIT_REMOTE_NAME, collection_git_url(collection_uid))
    
    # create collection files/ dir if not already present
    # mets.xml
    # control
    # changelog
    # commit
    # control
    collection_uid = os.path.basename(collection_path)

    entity_path_rel = os.path.join('files', entity_uid)
    entity_path_abs = os.path.join(collection_path, entity_path_rel)
        
    # entity dir
    if not os.path.exists(entity_path_abs):
        os.makedirs(entity_path_abs)
    
    git_files = []
    # entity control
    control_path_rel = os.path.join(entity_path_rel, 'control')
    control_path_abs = os.path.join(collection_path, control_path_rel)
    EntityControlFile.create(control_path_abs, collection_uid, entity_uid)
    if os.path.exists(control_path_abs):
        git_files.append(control_path_rel)
    else:
        logging.error('    COULD NOT CREATE control')
    # entity mets.xml
    mets_path_rel = os.path.join(entity_path_rel, 'mets.xml')
    mets_path_abs = os.path.join(collection_path, mets_path_rel)
    METS.create(mets_path_abs)
    if os.path.exists(mets_path_abs):
        git_files.append(mets_path_rel)
    else:
        logging.error('    COULD NOT CREATE mets')
    # entity changelog
    entity_changelog_path_rel = os.path.join(entity_path_rel, 'changelog')
    entity_changelog_path_abs = os.path.join(collection_path, entity_changelog_path_rel)
    entity_changelog_messages = ['Initialized entity {}'.format(entity_uid),]
    write_changelog_entry(
        entity_changelog_path_abs,
        entity_changelog_messages,
        user=user_name, email=user_mail)
    if os.path.exists(entity_changelog_path_abs):
        git_files.append(entity_changelog_path_rel)
    else:
        logging.error('    COULD NOT CREATE changelog')
    # update collection ead.xml
    ead = EAD(collection)
    ead.update_dsc(collection)
    ead.write()
    git_files.append(ead.filename)
    # update collection changelog
    changelog_path_rel = 'changelog'
    changelog_path_abs = os.path.join(collection_path, changelog_path_rel)
    changelog_messages = ['Initialized entity {}'.format(entity_uid),]
    write_changelog_entry(
        changelog_path_abs,
        changelog_messages,
        user=user_name, email=user_mail)
    git_files.append(changelog_path_rel)
    # update collection control
    ctl = CollectionControlFile(os.path.join(collection.path,'control'))
    ctl.update_checksums(collection)
    ctl.write()
    git_files.append('control')
    # add files and commit
    repo = commit_files(repo, changelog_messages[0], git_files, [])
    return 0,'ok'


@command
@local_only
def entity_destroy():
    """Command-line function for removing the specified entity from the collection.
    
    @return: message ('ok' if successful)
    """
    return 1,'not implemented yet'


@command
@local_only
def entity_update(user_name, user_mail, collection_path, entity_uid, updated_files):
    """Command-line function for committing changes to the specified entity file.
    
    NOTE: Does not push to the workbench server.
    Updates entity changelog but NOT in collection changelog.
    Makes an entry in git log.
    
    @param user_name: Username for use in changelog, git log
    @param user_mail: User email address for use in changelog, git log
    @param collection_path: Absolute path to collection repo.
    @param entity_uid: A valid DDR entity UID
    @param updated_files: List of paths to updated file(s), relative to entity/files.
    @return: message ('ok' if successful)
    """
    repo = git.Repo(collection_path)
    repo.git.checkout('master')
    repo.git.config('user.name', user_name)
    repo.git.config('user.email', user_mail)
    repo.git.config('annex.sshcaching', 'false')
    if not GIT_REMOTE_NAME in [r.name for r in repo.remotes]:
        repo.create_remote(GIT_REMOTE_NAME, collection_git_url(collection_uid))

    entity_path_rel = os.path.join('files', entity_uid)
    entity_path_abs = os.path.join(collection_path, entity_path_rel)
    # entity file paths are relative to collection root
    git_files = []
    for f in updated_files:
        git_files.append( os.path.join( 'files', entity_uid, f) )
    
    # entity changelog
    entity_changelog_path_rel = os.path.join(entity_path_rel, 'changelog')
    entity_changelog_path_abs = os.path.join(collection_path, entity_changelog_path_rel)
    entity_changelog_messages = []
    for f in updated_files:
        p = os.path.join(entity_uid, f)
        entity_changelog_messages.append('Updated entity file {}'.format(p))
    write_changelog_entry(
        entity_changelog_path_abs,
        entity_changelog_messages,
        user=user_name, email=user_mail)
    git_files.append(entity_changelog_path_rel)
    # add files and commit
    repo = commit_files(repo, 'Updated entity file(s)', git_files, [])
    return 0,'ok'


@command
@local_only
def entity_annex_add(user_name, user_mail, collection_path, entity_uid, new_file):
    """Command-line function for git annex add-ing a file and updating metadata.
    
    All this function does is git annex add the file, update changelog and
    mets.xml, and commit.
    It does not copy the file into the entity dir.
    It does not mark the file as master/mezzanine/access/etc or edit any metadata.
    It does not perform any background processing on the file.
    
    @param user_name: Username for use in changelog, git log
    @param user_mail: User email address for use in changelog, git log
    @param collection_path: Absolute path to collection repo.
    @param entity_uid: A valid DDR entity UID
    @param file_path: Path to new file relative to entity files dir.
    @return: message ('ok' if successful)
    """
    repo = git.Repo(collection_path)
    repo.git.checkout('master')
    repo.git.config('user.name', user_name)
    repo.git.config('user.email', user_mail)
    repo.git.config('annex.sshcaching', 'false')
    if not GIT_REMOTE_NAME in [r.name for r in repo.remotes]:
        repo.create_remote(GIT_REMOTE_NAME, collection_git_url(collection_uid))
    
    if not os.path.exists(os.path.join(collection_path, '.git', 'annex')):
        logging.error('    .git/annex IS MISSING!')
        return 1,'.git/annex IS MISSING!'

    entity_path_rel = os.path.join('files', entity_uid)
    entity_path_abs = os.path.join(collection_path, entity_path_rel)
    entity_files_rel = os.path.join(entity_path_rel, 'files')
    entity_files_abs = os.path.join(entity_path_abs, 'files')
    # absolute path to new file
    new_file_abs = os.path.join(entity_files_abs, new_file)
    # relative to collection repo
    new_file_rel = os.path.join(entity_files_rel, new_file)
    # relative to entity_dir
    new_file_rel_entity = new_file_abs.replace('{}/'.format(entity_path_abs), '')
    logging.debug('    new_file_abs {}'.format(new_file_abs))
    logging.debug('    new_file_rel {}'.format(new_file_rel))
    logging.debug('    new_file_rel_entity {}'.format(new_file_rel_entity))
    if not os.path.exists(entity_path_abs):
        logging.error('    Entity does not exist: {}'.format(entity_uid))
        return 1,'entity does not exist: {}'.format(entity_uid)
    if not os.path.exists(entity_files_abs):
        os.makedirs(entity_files_abs)
    if not os.path.exists(new_file_abs):
        logging.error('    File does not exist: {}'.format(new_file_abs))
        return 1,'File does not exist: {}'.format(new_file_abs)
    
    git_files = []
    # update entity changelog
    entity_changelog_path_rel = os.path.join(entity_path_rel, 'changelog')
    entity_changelog_path_abs = os.path.join(collection_path, entity_changelog_path_rel)
    changelog_messages = []
    for f in [new_file_rel_entity]:
        changelog_messages.append('Added entity file {}'.format(f))
    write_changelog_entry(
        entity_changelog_path_abs,
        changelog_messages,
        user_name, user_mail)
    git_files.append(entity_changelog_path_rel)
    # update entity control
    entity_control_path_rel = os.path.join(entity_path_rel,'control')
    entity_control_path_abs = os.path.join(entity_path_abs,'control')
    e = Entity(entity_path_abs)
    c = EntityControlFile(entity_control_path_abs)
    c.update_checksums(e)
    c.write()
    git_files.append(entity_control_path_rel)
    # update entity mets
    entity_mets_path_rel = os.path.join(entity_path_rel,'mets.xml')
    entity_mets_path_abs = os.path.join(entity_path_abs,'mets.xml')
    m = METS(e)
    m.update_filesec(e)
    m.write()
    git_files.append(entity_mets_path_rel)

    # add files and commit
    repo = commit_files(repo, 'Added entity file(s)', git_files, [new_file_rel])
#    # git annex add
#    logging.debug('    git annex add {}'.format(new_file_rel))
#    repo.git.annex('add', new_file_rel)
#    # TODO confirm new file actually added to git annex
#    # git add
#    for f in git_files:
#        logging.debug('    git add {}'.format(f))
#    repo.index.add(git_files)
#    # commit
#    commit = repo.index.commit('Added entity file(s)')
    return 0,'ok'


@command
@local_only
def entity_add_master(user_name, user_mail, collection_path, entity_uid, file_path):
    """Wrapper around entity_annex_add() that 
    
    @param user_name: Username for use in changelog, git log
    @param user_mail: User email address for use in changelog, git log
    @param collection_path: Absolute path to collection repo.
    @param entity_uid: A valid DDR entity UID
    @param file_path: Path to new file relative to entity files dir.
    @return: message ('ok' if successful)
    """
    return 1,'not implemented yet'


@command
@local_only
def entity_add_mezzanine(user_name, user_mail, collection_path, entity_uid, file_path):
    """
    @param user_name: Username for use in changelog, git log
    @param user_mail: User email address for use in changelog, git log
    @param collection_path: Absolute path to collection repo.
    @param entity_uid: A valid DDR entity UID
    @param file_path: Path to new file relative to entity files dir.
    @return: message ('ok' if successful)
    """
    return 1,'not implemented yet'


@command
@local_only
def entity_add_access(user_name, user_mail, collection_path, entity_uid, file_path):
    """
    @param user_name: Username for use in changelog, git log
    @param user_mail: User email address for use in changelog, git log
    @param collection_path: Absolute path to collection repo.
    @param entity_uid: A valid DDR entity UID
    @param file_path: Path to new file relative to entity files dir.
    @return: message ('ok' if successful)
    """
    return 1,'not implemented yet'


@command
@requires_network
def annex_push(collection_path, file_path_rel):
    """Push a git-annex file to workbench.

    Example file_paths:
        ddr-densho-1-1/files/video1.mov
        ddr-densho-42-17/files/image35.jpg
        ddr-one-35-248/files/newspaper.pdf
    
    $ git annex copy PATH --to=REMOTE
    
    @param collection_path: Absolute path to collection repo.
    @param file_path_rel: Path to file relative to collection root
    @return: message ('ok' if successful)
    """
    annex_path = os.path.join(collection_path, '.git', 'annex')
    file_path_abs = os.path.join(collection_path, file_path_rel)
    logging.debug('    collection_path {}'.format(collection_path))
    logging.debug('    file_path_rel {}'.format(file_path_rel))
    logging.debug('    file_path_abs {}'.format(file_path_abs))
    if not os.path.exists(collection_path):
        logging.error('    NO COLLECTION AT {}'.format(collection_path))
        return 1,'no collection'
    if not os.path.exists(annex_path):
        logging.error('    NO GIT ANNEX AT {}'.format(annex_path))
        return 1,'no annex'
    if not os.path.exists(file_path_abs):
        logging.error('    NO FILE AT {}'.format(file_path_abs))
        return 1,'no file'
    # let's do this thing
    repo = git.Repo(collection_path)
    repo.git.checkout('master')
    if not GIT_REMOTE_NAME in [r.name for r in repo.remotes]:
        repo.create_remote(GIT_REMOTE_NAME, collection_git_url(collection_uid))
    logging.debug('    git annex copy -t {} {}'.format(GIT_REMOTE_NAME, file_path_rel))
    stdout = repo.git.annex('copy', '-t', GIT_REMOTE_NAME, file_path_rel)
    logging.debug('\n{}'.format(stdout))
    # confirm that it worked
    remotes = annex_whereis_file(repo, file_path_rel)
    logging.debug('    present in remotes {}'.format(remotes))
    logging.debug('    it worked: {}'.format(GIT_REMOTE_NAME in remotes))
    logging.debug('    DONE')
    return 0,'ok'


@command
@requires_network
def annex_pull(collection_path, file_path_rel):
    """git-annex copy a file from workbench.

    Example file_paths:
        ddr-densho-1-1/files/video1.mov
        ddr-densho-42-17/files/image35.jpg
        ddr-one-35-248/files/newspaper.pdf
        
    @param collection_path: Absolute path to collection repo.
    @param file_path_rel: Path to file relative to collection root.
    @return: message ('ok' if successful)
    """
    annex_path = os.path.join(collection_path, '.git', 'annex')
    file_path_abs = os.path.join(collection_path, file_path_rel)
    logging.debug('    collection_path {}'.format(collection_path))
    logging.debug('    file_path_rel {}'.format(file_path_rel))
    logging.debug('    file_path_abs {}'.format(file_path_abs))
    if not os.path.exists(collection_path):
        logging.error('    NO COLLECTION AT {}'.format(collection_path))
        return 1,'no collection'
    if not os.path.exists(annex_path):
        logging.error('    NO GIT ANNEX AT {}'.format(annex_path))
        return 1,'no annex'
    # let's do this thing
    repo = git.Repo(collection_path)
    repo.git.checkout('master')
    if not GIT_REMOTE_NAME in [r.name for r in repo.remotes]:
        repo.create_remote(GIT_REMOTE_NAME, collection_git_url(collection_uid))
    logging.debug('    git annex copy -t {} {}'.format(GIT_REMOTE_NAME, file_path_rel))
    stdout = repo.git.annex('copy', '-f', GIT_REMOTE_NAME, file_path_rel)
    logging.debug('\n{}'.format(stdout))
    # confirm that it worked
    exists = os.path.exists(file_path_abs)
    lexists = os.path.lexists(file_path_abs)
    islink = os.path.islink(file_path_abs)
    itworked = (exists and lexists and islink)
    logging.debug('    it worked: {}'.format(itworked))
    logging.debug('    DONE')
    return 0,'ok'