import os
import tempfile

from fabric.api import *

import pantheon

class PantheonBackup():
    
    def __init__(self, name, project):
        self.server = pantheon.PantheonServer()
        self.working_dir = tempfile.mkdtemp()
        self.project =  project
        self.name = name


    def backup_files(self, environments=pantheon.get_environments()):
        for env in environments:
            source = os.path.join(self.server.webroot, self.project, env)
            dest = os.path.join(self.working_dir, self.project)
            local('mkdir -p %s' % dest)
            local('rsync -avz %s %s' % (source, dest))


    def backup_data(self, environments=pantheon.get_environments()):
        for env in environments:
            drupal_vars = self._parse_vhost(self.server.get_vhost_file(
                                                    self.project, env))
            dest = os.path.join(self.working_dir, self.project, env, 'database.sql')
            self._dump_data(dest, drupal_vars)


    def backup_repo(self):
        dest = os.path.join(self.working_dir, '%s.git' % (self.project))
        local('mkdir -p %s' % dest)
        local('git clone --mirror /var/git/projects -b %s %s/' % (self.project, dest))
        #TODO: warning: Remote branch master not found in upstream origin, using HEAD instead


    def make_archive(self):
        self.name = self.name + '.tar.gz'
        with cd(self.working_dir):
            local('tar czf %s %s %s' % (self.name, self.project, '%s.git' % (self.project)))


    def move_archive(self):
        with cd(self.working_dir):
            local('mv %s %s' % (self.name, self.server.ftproot))


    def _dump_data(self, destination, db_dict):        
        result = local("mysqldump --single-transaction \
                                  --user='%s' --password='%s' %s > %s" % (
                                         db_dict.get('db_username'),
                                         db_dict.get('db_password'),
                                         db_dict.get('db_name'),
                                         destination))
        if result.failed:
            abort("Export of database '%s' failed." % db_dict.get('db_name'))
                                         

    def _parse_vhost(self, path):
        env_vars = dict()
        with open(path, 'r') as f:
           vhost = f.readlines()
        for line in vhost:
            line = line.strip()
            if line.find('SetEnv') != -1:
                var = line.split()
                env_vars[var[1]] = var[2]
        return env_vars

