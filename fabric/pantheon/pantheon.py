# vim: tabstop=4 shiftwidth=4 softtabstop=4
import copy
import os
import random
import re
import string
import tempfile
import time
import urlparse

from fabric.api import *

ENVIRONMENTS = set(['dev','test','live'])


def get_environments():
    """ Return list of development environments.
   
    """
    return ENVIRONMENTS


def random_string(length):
    """ Create random string of ascii letters & digits.
    length: Int. Character length of string to return.

    """
    return ''.join(['%s' % random.choice (string.ascii_letters + \
                                          string.digits) \
                                          for i in range(length)])


def create_pantheon_settings_file(site_dir):
    with open(os.path.join(site_dir, 'settings.php'), 'a') as f:
        f.write('\n/* Added by Pantheon */\n')
        f.write("include 'pantheon.settings.php';\n")
    local('cp /opt/pantheon/fabric/templates/pantheon.settings.php ' + site_dir)


def export_data(project, environment, destination):
    filename = os.path.join(destination, '%s_%s.sql' % (project, environment))
    username, password, db_name = _get_database_vars(project, environment)
    local("mysqldump --single-transaction --user='%s' \
                                          --password='%s' \
                                            %s > %s" % (username,
                                                       password,
                                                       db_name,
                                                       filename))
    return filename

    
def import_data(project, environment, source):
    username, password, db_name = _get_database_vars(project, environment)

    local("mysql -u root -e 'DROP DATABASE IF EXISTS %s'" % db_name)
    local("mysql -u root -e 'CREATE DATABASE %s'" % db_name)
    # Strip cache tables, convert MyISAM to InnoDB, and import.
    local("cat %s | grep -v '^INSERT INTO `cache[_a-z]*`' | \
           grep -v '^INSERT INTO `ctools_object_cache`' | \
           grep -v '^INSERT INTO `watchdog`' | \
           grep -v '^INSERT INTO `accesslog`' | \
           grep -v '^USE `' | \
           mysql -u root %s" % (source, db_name))


def parse_vhost(path):
    env_vars = dict()
    with open(path, 'r') as f:
       vhost = f.readlines()
    for line in vhost:
        line = line.strip()
        if line.find('SetEnv') != -1:
            var = line.split()
            env_vars[var[1]] = var[2]
    return env_vars


def restart_bcfg2():
    local('/etc/init.d/bcfg2-server restart')
    server_running = False
    warn('Waiting for bcfg2 server to start')
    while not server_running:
        with settings(hide('warnings'), warn_only=True):
            server_running = (local('netstat -atn | grep :6789')).rstrip('\n')
        time.sleep(5)


def _get_database_vars(project, environment):
    vhost = PantheonServer().get_vhost_file(project, environment)
    env_vars = parse_vhost(vhost)
    return (env_vars.get('db_username'), 
            env_vars.get('db_password'), 
            env_vars.get('db_name'))


class PantheonServer:

    def __init__(self):
        # Ubuntu / Debian
        if os.path.exists('/etc/debian_version'):
            self.distro = 'ubuntu'
            self.mysql = 'mysql'
            self.owner = 'root'
            self.web_group = 'www-data'
            self.hudson_group = 'nogroup'
            self.tomcat_owner = 'tomcat6'
            self.tomcat_version = '6'
            self.webroot = '/var/www/'
            self.ftproot = '/srv/ftp/pantheon/'
            self.vhost_dir = '/etc/apache2/sites-available/'
        # Centos
        elif os.path.exists('/etc/redhat-release'):
            self.distro = 'centos'
            self.mysql = 'mysqld'
            self.owner = 'root'
            self.web_group = 'apache'
            self.hudson_group = 'hudson'
            self.tomcat_owner = 'tomcat'
            self.tomcat_version = '5'
            self.webroot = '/var/www/html/'
            self.ftproot = '/var/ftp/pantheon/'
            self.vhost_dir = '/etc/httpd/conf/vhosts/'


    def get_hostname(self):
        if os.path.exists("/usr/local/bin/ec2-metadata"):
            return local('/usr/local/bin/ec2-metadata -p | sed "s/public-hostname: //"').rstrip('\n')
        else:
            return local('hostname').rstrip('\n')


    def update_packages(self):
        if (self.distro == "centos"):
            local('yum clean all')
            local('yum -y update')
        else:
            local('apt-get -y update')
            local('apt-get -y dist-upgrade')


    def restart_services(self):
        if self.distro == 'ubuntu':
            local('/etc/init.d/apache2 restart')
            local('/etc/init.d/memcached restart')
            local('/etc/init.d/tomcat6 restart')
            local('/etc/init.d/varnish restart')
            local('/etc/init.d/mysql restart')
        elif self.distro == 'centos':
            local('/etc/init.d/httpd restart')
            local('/etc/init.d/memcached restart')
            local('/etc/init.d/tomcat5 restart')
            local('/etc/init.d/varnish restart')
            local('/etc/init.d/mysqld restart')


    def setup_iptables(self, file):
        local('/sbin/iptables-restore < ' + file)
        local('/sbin/iptables-save > /etc/iptables.rules')


    def create_drush_alias(self, drush_dict):
        """ Create an alias.drushrc.php file.
        drush_dict: project:
                    environment:
                    vhost_path: full path to vhost file
                    root: full path to drupal installation
        
        """
        alias_template = '/opt/pantheon/fabric/templates/drush.alias.drushrc.php'
        alias_file = '/opt/drush/aliases/%s_%s.alias.drushrc.php' % (
                                            drush_dict.get('project'), 
                                            drush_dict.get('environment'))
        template = self._build_template(alias_template, drush_dict)
        with open(alias_file, 'w') as f:
            f.write(template)
        

    def create_vhost(self, filename, vhost_dict):
        """ 
        filename:  vhost filename
        vhost_dict: project:
                    environment:
                    db_name:
                    db_username:
                    db_password:
                    db_solr_path:
                    memcache_prefix:

        """
        vhost_template = local("cat /opt/pantheon/fabric/templates/vhost.template.%s" % self.distro)
        template = string.Template(vhost_template)
        template = template.safe_substitute(vhost_dict)
        vhost = os.path.join(self.vhost_dir, filename)
        with open(vhost, 'w') as f:
            f.write(template)
        local('chmod 640 %s' % vhost)
        

    def create_solr_index(self, project, environment):
        """ Create solr index in: /var/solr/project/environment.
        project: project name
        environment: development environment

        """
        data_dir_template = '/opt/pantheon/fabric/templates/solr/'
        tomcat_template = local("cat /opt/pantheon/fabric/templates/tomcat_solr_home.xml")

        # Create project directory
        project_dir = '/var/solr/%s/' % project
        if not os.path.exists(project_dir):
            local('mkdir %s' % project_dir)
        
        # Create data directory from sample solr data.
        data_dir = project_dir + environment
        if os.path.exists(data_dir):
            local('rm -rf ' + data_dir)
        local('cp -R %s %s' % (data_dir_template, data_dir))

        local('chown -R %s:%s %s' % (self.tomcat_owner,
                                     self.tomcat_owner,
                                     project_dir))

        # Tell Tomcat where indexes are located.
        template = string.Template(tomcat_template)
        solr_path = '%s/%s' % (project, environment)
        template = template.safe_substitute({'solr_path':solr_path})
        tomcat_file = "/etc/tomcat%s/Catalina/localhost/%s_%s.xml" % (
                                                      self.tomcat_version,
                                                      project,
                                                      environment)
        with open(tomcat_file, 'w') as f:
            f.write(template)
        local('chown %s:%s %s' % (self.tomcat_owner,
                                  self.tomcat_owner,
                                  tomcat_file))


    def create_drupal_cron(self, project, environment):
        """ Create Hudson drupal cron job.
        project: project name
        environment: development environment

        """
        # Create job directory
        jobdir = '/var/lib/hudson/jobs/cron_%s_%s/' % (project, environment)
        if not os.path.exists(jobdir):
            local('mkdir -p ' + jobdir)
 
        # Create job from template
        cron_template = local("cat /opt/pantheon/fabric/templates/hudson.drupal.cron")
        site_path = os.path.join(self.webroot, '%s/%s' % (project, environment))
        template = string.Template(cron_template)
        template = template.safe_substitute({'site_path':site_path})
        with open(jobdir + 'config.xml', 'w') as f:
            f.write(template)

        # Set Perms
        local('chown -R %s:%s %s' % ('hudson', self.hudson_group, jobdir))     


    def get_vhost_file(self, project, environment):
        filename = '%s_%s' % (project, environment)
        if environment == 'live':
            filename = '000_' + filename
        if self.distro == 'ubuntu':
            return '/etc/apache2/sites-available/%s' % filename
        elif self.distro == 'centos':
            return '/etc/httpd/conf/vhosts/%s' % filename


    def _build_template(self, template_file, values):
        """ Helper method that returns a template object of the template_file 
            with substitued values.
        filename: full path to template file
        values: dictionary of values to be substituted in template file

        """
        contents = local('cat %s' % template_file)
        template = string.Template(contents)
        template = template.safe_substitute(values)
        return template

