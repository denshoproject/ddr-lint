import os
import shutil
import subprocess
import sys
import unittest

CMD_PATH = os.path.join(sys.path[0], '..', 'entity')

TEST_TMP_PATH = '/tmp/'
TEST_UID = 'ddr-densho-1-1'
TEST_FILES_DIR = os.path.join(sys.path[0], 'files')
TEST_USER_NAME = 'gjost'
TEST_USER_MAIL = 'geoffrey.jost@densho.org'

TEST_ENTITY = os.path.join(TEST_TMP_PATH,TEST_UID)
ENTITY_CHANGELOG = os.path.join(TEST_ENTITY, 'changelog')
ENTITY_CONTROL = os.path.join(TEST_ENTITY, 'control')
ENTITY_METS = os.path.join(TEST_ENTITY, 'mets.xml')

TEST_FILES = [
    {'file':   '20121205.jpg',
     'sha1':   'c07a01ce976885e56138e821b3063a5ba2e97078',
     'md5':    '42d55eb5ac104c86655b3382213deef1',
     'size':   '12457',},
    {'file':   '6a00e55055.png',
     'sha1':   'a58d0c947a747a9bce655938b5c251f72a377c00',
     'md5':    'fadfbcd8ceb71b9cfc765b9710db8c2c',
     'size':   '6539',},
    ]

class TestEntity(unittest.TestCase):

    def setUp(self):
        pass

    # initialize -------------------------------------------------------
    
    def test_00init(self):
        #print('\nentity init')
        if os.path.exists(TEST_ENTITY):
            shutil.rmtree(TEST_ENTITY, ignore_errors=True)
        #
        cmd = '{} -u {} -m {} -e {} -o init'.format(CMD_PATH, TEST_USER_NAME, TEST_USER_MAIL, TEST_ENTITY)
        #print(cmd)
        out = subprocess.check_output(cmd, shell=True)
        #print(out)
        
        # directories exist
        self.assertTrue(os.path.exists(TEST_ENTITY))
        self.assertTrue(os.path.exists(ENTITY_CHANGELOG))
        self.assertTrue(os.path.exists(ENTITY_CONTROL))
        self.assertTrue(os.path.exists(ENTITY_METS))
        # git, git-annex
        git = os.path.join(TEST_ENTITY,'.git')
        annex = os.path.join(git, 'annex')
        self.assertTrue(os.path.exists(git))
        self.assertTrue(os.path.exists(annex))

    # add --------------------------------------------------------------

    def test_01add(self):
        """Add files to entity, ensure they were added.
        """
        #print('\nentity add')
        for ffile in TEST_FILES:
            f = os.path.join(TEST_FILES_DIR, ffile['file'])
            cmd = '{} -u {} -m {} -e {} -o add -f {}'.format(CMD_PATH, TEST_USER_NAME, TEST_USER_MAIL, TEST_ENTITY, f)
            #print(cmd)
            out = subprocess.check_output(cmd, shell=True)
            #print(out)

        # files dir
        ENTITY_FILES_DIR = os.path.join(TEST_ENTITY, 'files')
        self.assertTrue(os.path.exists(ENTITY_FILES_DIR))
        
        for ffile in TEST_FILES:
            ffile['abs'] = os.path.join(ENTITY_FILES_DIR, ffile['file'])
            # each file should exist...
            self.assertTrue(os.path.exists(ffile['abs']))
            # and should be a git-annex file
            self.assertTrue(os.path.islink(ffile['abs']))
            

    def test_01add_changelog(self):
        """Checks that each added file appears in changelog
        """
        changelog = ''
        with open(ENTITY_CHANGELOG, 'r') as ch:
            changelog = ch.read()
        for ffile in TEST_FILES:
            ffile['rel'] = os.path.join('files', ffile['file'])
            changelog_entry = '* Added file: {}'.format(ffile['rel'])
            self.assertTrue(changelog_entry in changelog)

    def test_01add_control(self):
        """Checks that each added file appears in control
        """
        control = ''
        with open(ENTITY_CONTROL, 'r') as co:
            control = co.read()
        for ffile in TEST_FILES:
            ffile['rel'] = os.path.join('files', ffile['file'])
            control_sha1 = '{} = {}'.format(ffile['sha1'], ffile['rel'])
            control_md5 = '{} = {} ; {}'.format(ffile['md5'], ffile['size'], ffile['rel'])
            self.assertTrue(control_sha1 in control)
            self.assertTrue(control_md5 in control)

    def test_01add_mets(self):
        """Checks that each added file appears in mets.xml
        """
        mets = ''
        with open(ENTITY_METS, 'r') as mx:
            mets = mx.read()
        for ffile in TEST_FILES:
            ffile['rel'] = os.path.join('files', ffile['file'])
            mets_md5 = 'CHECKSUM="{}"'.format(ffile['md5'])
            mets_href = 'href="{}"'.format(ffile['rel'])
            self.assertTrue(mets_md5 in mets)
            self.assertTrue(mets_href in mets)

    # remove -----------------------------------------------------------


if __name__ == '__main__':
    unittest.main()