import sys

sys.path.append('/opt/pantheon/fab')
from pantheon import pantheon

from fabric.api import *

class BuildTools(object):
    """ Generic Pantheon project installation helper functions.

    """

    def __init__(self, project):
        """ Initialize generic project installation object & helper functions.
        project: the name of the project to be built.

        """
        self.server = pantheon.PantheonServer()

        self.project = project
        self.environments = pantheon.get_environments()
        self.project_path = os.path.join(self.server.webroot, project))

    def setup_database(self, environment, password, db_dump=None):
        username = self.project
        database = '%s_%s' % (self.project, environment)

        pantheon.create_database(database)
        pantheon.set_database_grants(database, username, password)
        if db_dump:
            pantheon.import_db_dump(db_dump, database)

    def setup_drush_alias(self):
        """ Create drush aliases for each environment in a project.

        """
        for env in self.environments:
            vhost = self.server.get_vhost_file(self.project, env)
            root = os.path.join(self.server.webroot, self.project, env)
            drush_dict = {'project': self.project,
                          'environment': env,
                          'vhost_path': vhost,
                          'root': root}
            self.server.create_drush_alias(drush_dict)

    def setup_solr_index(self):
        """ Create solr index for each environment in a project.

        """
        for env in self.environments:
            self.server.create_solr_index(self.project, env)

    def setup_vhost(self, db_password):
        """ Create vhost files for each environment in a project.

        """
        for env in self.environments:

            if pantheon.is_private_server():
                server_alias = '%s.*' % env
            else:
                server_alias = '%s.*.gotpantheon.com' % env

            vhost_dict = {'server_name': env,
                          'server_alias': server_alias,
                          'project': self.project,
                          'environment': env,
                          'db_name': '%s_%s' % (self.project, env),
                          'db_username':self.project,
                          'db_password':db_password,
                          'solr_path': '/%s_%s' % (self.project, env),
                          'memcache_prefix': '%s_%s' % (self.project, env)}

            filename = '%s_%s' % (self.project, env)
            if env == 'live':
                filename = '000_' + filename

            self.server.create_vhost(filename, vhost_dict)
            if self.server.distro == 'ubuntu':
               local('a2ensite %s' % filename)

    def setup_drupal_cron(self):
        """ Create drupal cron jobs in hudson for each development environment.

        """
        for env in self.environments:
            self.server.create_drupal_cron(self.project, env)

    def setup_environments(self, tag):
       """ Clone project from central repo to all environments.
           environments: Optional. List.

       """
       local('rm -rf %s' % (os.path.join(self.server.webroot, self.project)))
       for env in self.environments:
           destination = os.path.join(self.project_path, env)
           local('git clone -l /var/git/projects/%s -b %s %s' % (self.project,
                                                                 self.project,
                                                                 destination))
           with cd(destination):
               if env == 'dev':
                   local('git checkout %s' % self.project)
               else:
                   local('git fetch')
                   local('git reset --hard %s.%s' % (self.project, tag))

    def push_to_repo(self, tag='initialization'):
        """ Commit changes in working directory and push to central repo.

        """
        with cd(self.working_dir):
            local('git checkout %s' % self.project)
            local('git add -A .')
            local("git commit --author=\"%s\" -m 'Initialize Project: %s'" % (
                                                   self.author, self.project))
            local('git tag %s.%s' % (self.project, tag))
            local('git push')
            local('git push --tags')

    def setup_permissions(self, handler, environment=None):
        """ Set permissions on project directory, settings.php, and files dir.

        """
        # Get  owner
        #TODO: Allow non-getpantheon users to set a default user.
        if os.path.exists("/etc/pantheon/ldapgroup"):
            owner = self.server.get_ldap_group()
        else:
            owner = self.server.web_group

        # During code updates, we only make changes in one environment.
        # Otherwise, in some cases we are modifying all environments.
        environments = list()
        if handler == 'update':
            #Single environment
            environments.append(environment)
        else:
            #All environments.
            environments = self.environments


        """
        Project directory and sub files/directories

        """

        # For new installs / imports / restores, use recursive chown.
        if handler in ['install', 'import', 'restore']:
            with cd(self.server.webroot):
                local('chown -R %s:%s %s' % (owner, owner, self.project))
                local('chmod -R g+w %s' % (self.project))

        # For code updates, be more specific (for performance reasons)
        elif handler == 'update':
            # Only make changes in the environment being updated.
            with cd(os.path.join(self.project_path,
                                 environments[0])):
                # Set ownership on everything exept files directory.
                #TODO: Restrict chown to files changed in git diff.
                local("find . \( -path ./sites/default/files -prune \) \
                       -o \( -exec chown %s:%s '{}' \; \)" % (owner, owner))


        """
        Files directory and sub files/directories

        """

        # For installs, just set 770 on files dir.
        if handler == 'install':
            for env in environments:
                site_dir = os.path.join(self.project_path,
                                        env,
                                        'sites/default')
                with cd(site_directory):
                    local('chmod 770 files')
                    local('chown %s:%s files' % (self.server.web_group,
                                                 self.server.web_group))

        # For imports or restores: 770 on files dir (and subdirs). 660 on files
        elif handler in ['import', 'restore']:
            for env in environments:
                file_dir = os.path.join(self.project_path, env,
                                        'sites/default/files')
                with cd(file_dir):
                    local("chmod 770 .")
                    # All sub-files
                    local("find . -type d -exec find '{}' -type f \; | \
                           while read FILE; do chmod 660 \"$FILE\"; done")
                    # All sub-directories
                    local("find . -type d -exec find '{}' -type d \; | \
                          while read DIR; do chmod 770 \"$DIR\"; done")
                    # Apache should own files/*
                    local("chown -R %s:%s ." % (self.server.web_group,
                                                self.server.web_group))

        # For updates, set apache as owner of files dir.
        elif handler == 'update':
            site_dir = os.path.join(self.project_path,
                                    environments[0],
                                    'sites/default')
            with cd(site_dir):
                local('chown %s:%s files' % (self.server.web_group,
                                             self.server.web_group))


        """
        settings.php & pantheon.settings.php

        """

        #TODO: We could split this up based on handler, but changing perms on
        # two files is fast. Ignoring for now, and treating all the same.
        for env in environments:
            if pantheon.is_drupal_installed(self.project, env):
                # Drupal installed, Apache does not need to own settings.php
                settings_perms = '440'
                settings_owner = owner
                settings_group = self.server.web_group
            else:
                # Drupal is NOT installed. Apache must own settings.php
                settings_perms = '660'
                settings_owner = self.server.web_group
                settings_group = self.server.web_group

            site_dir = os.path.join(self.project_path, env, 'sites/default')
            with cd(site_dir):
                # settings.php
                local('chmod %s settings.php' % settings_perms)
                local('chown %s:%s settings.php' % (settings_owner,
                                                    settings_group))
                # pantheon.settings.php
                local('chmod 440 pantheon.settings.php')
                local('chmod %s:%s pantheon.settings.php' % (owner,
                                                             settings_group))

                # Apache should own settings.php and pantheon.settings.php
                local('chown %s:%s settings.php pantheon.settings.php' % (
                                                    self.server.web_group,
                                                    self.server.web_group))
                # Apache should own files directory.
                local('chown -R %s:%s files' % (self.server.web_group,
                                                self.server.web_group))


