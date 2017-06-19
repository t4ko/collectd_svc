#!/usr/bin/env python
#
# vim: tabstop=4 shiftwidth=4

# This program is free software; you can redistribute it and/or modify it
# under the terms of the GNU General Public License as published by the
# Free Software Foundation; only version 2 of the License is applicable.
#
# This program is distributed in the hope that it will be useful, but
# WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the GNU
# General Public License for more details.
#
# You should have received a copy of the GNU General Public License along
# with this program; if not, write to the Free Software Foundation, Inc.,
# 51 Franklin St, Fifth Floor, Boston, MA  02110-1301 USA
#
# Authors:
#   Ricardo Rocha <ricardo@catalyst.net.nz>
#
# About this plugin:
#   This plugin collects information regarding Ceph pools.
#
# collectd:
#   http://collectd.org
# collectd-python:
#   http://collectd.org/documentation/manpages/collectd-python.5.shtml
# ceph pools:
#   http://ceph.com/docs/master/rados/operations/pools/
#

import collectd
import json
import re
import traceback
import random
import paramiko
import os
import time
import subprocess
import xml.etree.cElementTree as ET
from collections import defaultdict

import base

class SVCPlugin(base.Base):

    def __init__(self):
        base.Base.__init__(self)
        self.prefix = 'svc'

    def get_stats(self):
        """Retrieves stats from the svc cluster pools"""

        svc_cluster = self.cluster # Defines the name of the current svc cluster (provided in the conf)

        print("Beginning get_stats {0}".format(time.clock()))
        # Connect with ssh to svc
        ssh = paramiko.SSHClient()
        ssh.set_missing_host_key_policy(paramiko.client.AutoAddPolicy())
        ssh.connect(self.sshAdress, username=self.sshUser, key_filename=self.sshRSAkey)
        

        # Get the node list and the time at which all nodes made their iostats dump 
        (stdin, stdout, stderr) = ssh.exec_command('lsdumps -prefix /dumps/iostats/')
        firstNode = ''
        firstNodeTime = ''
        lastCompleteTime = ''
        lastCompleteDay = ''
        nodeList = set()
        lsdumpsList = set()
        dateObtained = 0
        for line in reversed(list(stdout)):
            lsdumpsList.add(line[4:-2])
            if dateObtained == 0:
                junk1, junk2, node, day, minute = line[:-2].split('_')
                if firstNode == '':
                    firstNode = node
                    firstNodeTime = minute
                    lastCompleteTime = minute
                    lastCompleteDay = day
                if node == firstNode and minute != firstNodeTime:
                    dateObtained = 1
                if node != firstNode and minute != firstNodeTime:
                    lastCompleteTime = minute
                    lastCompleteDay = day
                if node not in nodeList:
                    nodeList.add(node)
        print("Finish get last time and dump list {0}".format(time.clock()))

        # Compute old timestamp
        oldEpoch = time.mktime(time.strptime("{0}{1}".format(lastCompleteDay, lastCompleteTime[:]), "%y%m%d%H%M%S")) - int(self.interval) 
        oldTimeString = time.strftime("%y%m%d_%H%M%S", time.localtime(oldEpoch))

        # Create the dumps directory if it does not exist yet
        dumpsFolder = '{}/svc-stats-dumps'.format(os.getcwd())
        if not os.path.exists(dumpsFolder):
            os.makedirs(dumpsFolder)

        # Get the list of file currently in the directory
        dumpsList = os.listdir(dumpsFolder)
        useOld = 1
        oldDumpsList = set()
        for nodeId in nodeList:
            for statType in ['Nn', 'Nv', 'Nm']:
                oldFileName = "{0}_stats_{1}_{2}".format(statType, nodeId, oldTimeString)
                oldDumpsList.add(oldFileName)
                if oldFileName not in dumpsList:
                    useOld = 0

        # Download the previous file from the SVC cluster if they are available
        if useOld == 0:
            useOld = 1
            for oldFileName in oldDumpsList:
                if oldFileName not in lsdumpsList:
                    useOld = 0
            if useOld == 1:
                command = "scp -i {0} -o StrictHostKeyChecking=no -q '{1}@{2}:/dumps/iostats/*{3}' {4}".format(self.sshRSAkey, self.sshUser, self.sshAdress, oldTimeString, dumpsFolder)
                subprocess.call(command, shell=True)

                # for oldFileName in oldDumpsList:
                #     command = ['scp' ,'-i', self.sshRSAkey, '-o', 'StrictHostKeyChecking=no', '-q', '{0}@{1}:/dumps/iostats/{2}'.format(self.sshUser, self.sshAdress, oldFileName), '{0}/{1}'.format(dumpsFolder, oldFileName)]
                #     subprocess.call(command)
        print("Finish dl oldfiles {0}".format(time.clock()))

        # Load and parse previous files if they are available
        if useOld == 1:
            old_stats = defaultdict(dict)
            for filename in oldDumpsList :
                statType, junk1, nodeId, junk2, junk3 = filename.split('_')
                old_stats[nodeId][statType] = ET.parse('{0}/{1}'.format(dumpsFolder, filename)).getroot()
            for nodeId in nodeList:
                old_stats[nodeId]['sysid'] = old_stats[nodeId]['Nn'].get('id')

        print("finish parsing old files {0}".format(time.clock()))

        # Download the stats files from /dumps/iostats/ on svc and parse the xml
        stats = defaultdict(dict)
        command = "scp -i {0} -o StrictHostKeyChecking=no -q '{1}@{2}:/dumps/iostats/*{3}_{4}' {5}".format(self.sshRSAkey, self.sshUser, self.sshAdress, lastCompleteDay, lastCompleteTime, dumpsFolder)
        subprocess.call(command, shell=True)
        for nodeId in nodeList:
            for statType in ['Nn', 'Nv', 'Nm']:
                filename = '{0}_stats_{1}_{2}_{3}'.format(statType, nodeId, lastCompleteDay, lastCompleteTime)
                stats[nodeId][statType] = ET.parse('{0}/{1}'.format(dumpsFolder, filename)).getroot()
            stats[nodeId]['sysid'] = stats[nodeId]['Nn'].get('id')
        print("finish dl and parsing new files {0}".format(time.clock()))

        # Remove old stats files
        for filename in dumpsList:
            os.remove('{0}/{1}'.format(dumpsFolder, filename))










        # Load the MdiskGrp names and their Mdisk from the svc cluster if needed
        mdiskGrpList = { }
        mdiskList = { }
        (stdin_mdsk, stdout_mdsk, stderr_mdsk) = ssh.exec_command('lsmdisk -delim :')
        isFirst, nameIndex, mdisk_grp_nameIndex = True, -1, -1
        for line in stdout_mdsk:
            splittedLine = line.split(':')
            if isFirst:
                isFirst = False
                nameIndex, mdisk_grp_nameIndex = splittedLine.index('name'), splittedLine.index('mdisk_grp_name')
                continue
            if nameIndex == -1 or mdisk_grp_nameIndex == -1 or nameIndex == mdisk_grp_nameIndex:
                sys.exit('The first line of the output for \'lsmdisk -delim :\' is missing \'name\' or \'mdisk_grp_name\'')
            mdiskList[splittedLine[nameIndex]] = { 
                'mdiskGrpName' : splittedLine[mdisk_grp_nameIndex], 
                'ro' : 0, 
                'wo' : 0, 
                'rrp' : 0, 
                'wrp' : 0 
            }
            mdiskGrpList[splittedLine[mdisk_grp_nameIndex]] = {
                'ro' : 0, 
                'wo' : 0, 
                'rrp' : 0, 
                'wrp' : 0,
                'b_ro' : 0,
                'b_wo' : 0,
                'b_rrp': 0,
                'b_wrp': 0
            }


        # Load the vdisk and their mdisk group
        vdiskList = {}
        (stdin_vdsk, stdout_vdsk, stderr_vdsk) = ssh.exec_command('lsvdisk -delim :')
        isFirst, nameIndex, mdisk_grp_nameIndex = True, -1, -1
        for line in stdout_vdsk:
            splittedLine = line.split(':')
            if isFirst:
                isFirst = False
                nameIndex, mdisk_grp_nameIndex = splittedLine.index('name'), splittedLine.index('mdisk_grp_name')
                continue
            if nameIndex == -1 or mdisk_grp_nameIndex == -1 or nameIndex == mdisk_grp_nameIndex:
                sys.exit('The first line of the output for \'lsvdisk -delim :\' is missing \'name\' or \'mdisk_grp_name\'')
            vdiskList[splittedLine[nameIndex]] = { 
                'mdiskGrpName' : '', 
                'ro' : 0, 
                'wo' : 0, 
                'rrp' : 0, 
                'wrp' : 0 
            }
            
            if splittedLine[mdisk_grp_nameIndex] == 'many':
                (stdin_details, stdout_details, stderr_details) = ssh.exec_command('lsvdisk -delim : {}'.format(splittedLine[nameIndex]))
                for line_details in stdout_details:
                    if 'mdisk_grp_name' in line_details and not 'many' in line_details:
                        detailArray = line_details.split(':')
                        vdiskList[splittedLine[nameIndex]]['mdiskGrpName'] = detailArray[1].replace('\n', '')
                        break
            else:
                vdiskList[splittedLine[nameIndex]]['mdiskGrpName'] = splittedLine[mdisk_grp_nameIndex]










        ## Metrics for SVC nodes
        data = { svc_cluster : { 'node': {}, 'vdsk': {}, 'mdsk': {} } }
        # Initialize the structure for storing the collected data for nodes
        for nodeId in nodeList:
            data[svc_cluster]['node.{}'.format(stats[nodeId]['sysid'])] = { 'counter' : {}, 'gauge' : {} }
            data[svc_cluster]['node.{}'.format(stats[nodeId]['sysid'])]['counter'] = {
                'cpu_utilization' : 0,
                'read_data_rate' : 0, 
                'read_io_rate' : 0, 
                'write_data_rate' : 0, 
                'write_io_rate' : 0
            }
            data[svc_cluster]['node.{}'.format(stats[nodeId]['sysid'])]['gauge'] = {
                'read_response_time' : 0,
                'write_response_time' : 0,
                'write_cache_delay_percentage' : 0
            }
        # Initialize the structure for storing the collected data for mdisks
        for mdisk in mdiskList:
            data[svc_cluster]['mdsk.{}'.format(mdiskList[mdisk]['mdiskGrpName'])] = { 'counter' : {}, 'gauge' : {} }
            data[svc_cluster]['mdsk.{}'.format(mdiskList[mdisk]['mdiskGrpName'])]['counter'] = {
                'backend_read_data_rate' : 0,
                'backend_read_io_rate' : 0,
                'backend_write_data_rate' : 0,
                'backend_write_io_rate' : 0,
                'read_data_rate' : 0, 
                'read_io_rate' : 0, 
                'write_data_rate' : 0, 
                'write_io_rate' : 0
            }
            data[svc_cluster]['mdsk.{}'.format(mdiskList[mdisk]['mdiskGrpName'])]['gauge'] = {
                'backend_read_response_time' : 0,
                'backend_write_response_time' : 0,
                'read_response_time' : 0,
                'write_response_time' : 0
            }
        # Initialize the structure for storing the collected data for vdisks
        for vdisk in vdiskList:
            data[svc_cluster]['vdsk.{}'.format(vdisk)] = { 'counter' : {}, 'gauge' : {} }
            data[svc_cluster]['vdsk.{}'.format(vdisk)]['counter'] = {
                'read_io_rate' : 0,
                'write_io_rate' : 0,
                'read_data_rate' : 0,
                'write_data_rate' : 0
            }
            data[svc_cluster]['vdsk.{}'.format(vdisk)]['gauge'] = {
                'read_response_time' : 0, 
                'write_response_time' : 0
            }
        










        ## Iterate over the nodes to analyse their stats files
        countAppearance1 = {}
        countAppearance2 = {}
        for nodeId in nodeList:
            node_sysid = stats[nodeId]['sysid'] #for lisibility
            #node_ports = stats[nodeId]['Nn'].findall('{http://ibm.com/storage/management/performance/api/2006/01/nodeStats}port')
            node_vdisks = stats[nodeId]['Nv'].findall('{http://ibm.com/storage/management/performance/api/2005/08/vDiskStats}vdsk')
            node_mdisks = stats[nodeId]['Nm'].findall('{http://ibm.com/storage/management/performance/api/2003/04/diskStats}mdsk')


        # CPU utilization : Nn File > cpu > busy (Extract the counter)
            cpu_utilization = stats[nodeId]['Nn'].find('{http://ibm.com/storage/management/performance/api/2006/01/nodeStats}cpu').get('busy')
            data[svc_cluster]['node.{}'.format(node_sysid)]['counter']['cpu_utilization'] = int(cpu_utilization) 


        # read_data_rate : Nm file > mdsk > rb (512 bytes sector write)
        # read_io_rate : Nm file > mdsk > ro (read operation)
        # write_data_rate : Nm file > mdsk > wb (512 bytes blocks write)
        # write_io_rate : Nm file > mdsk > wo (write operation)
            for mdisk in node_mdisks:
                data[svc_cluster]['node.{}'.format(node_sysid)]['counter']['read_data_rate'] += (int(mdisk.get('rb')) * 512)
                data[svc_cluster]['node.{}'.format(node_sysid)]['counter']['read_io_rate'] += int(mdisk.get('ro'))
                data[svc_cluster]['node.{}'.format(node_sysid)]['counter']['write_data_rate'] += (int(mdisk.get('wb')) * 512)
                data[svc_cluster]['node.{}'.format(node_sysid)]['counter']['write_io_rate'] += int(mdisk.get('wo'))
        

        # read_response_time : Nm file > mdsk > ure (read external response time (microsecond))
        # write_response_time : Nm file > mdsk > uwe (write external response time (microsecond))
            total_rrp, total_ro, total_wrp, total_wo, mdisks_count = 0, 0, 0, 0, len(node_mdisks)
            if useOld == 1:
                old_node_mdisks = set(old_stats[nodeId]['Nm'].findall('{http://ibm.com/storage/management/performance/api/2003/04/diskStats}mdsk'))
                for mdisk in node_mdisks:
                    total_ro += int(mdisk.get('ro'))
                    total_wo += int(mdisk.get('wo'))
                    total_rrp += int(mdisk.get('re'))
                    total_wrp += int(mdisk.get('we'))
                for mdisk in old_node_mdisks:
                    total_ro -= int(mdisk.get('ro'))
                    total_wo -= int(mdisk.get('wo'))
                    total_rrp -= int(mdisk.get('re'))
                    total_wrp -= int(mdisk.get('we'))
                if total_ro == 0: #avoid division by 0
                    data[svc_cluster]['node.{}'.format(node_sysid)]['gauge']['read_response_time'] = 0
                else :
                    data[svc_cluster]['node.{}'.format(node_sysid)]['gauge']['read_response_time'] = float(total_rrp/total_ro)
                if total_wo == 0: #avoid division by 0
                    data[svc_cluster]['node.{}'.format(node_sysid)]['gauge']['write_response_time'] = 0
                else :
                    data[svc_cluster]['node.{}'.format(node_sysid)]['gauge']['write_response_time'] =float(total_wrp/total_wo)


        # write_cache_delay_percentage : Nv file > vdsk > ctwft + ctwwt (flush-through + write through)
        # write_cache_delay_percentage not possible without accessing previous data, suggest using write_cache_delay_rate
            
            write_cache_delay_percentage, ctw, ctwft, ctwwt = 0, 0, 0, 0
            if useOld == 1:
                for vdisk in node_vdisks:
                    old_vdisk = old_stats[nodeId]['Nv'].find('{0}vdsk[@idx="{1}"]'.format('{http://ibm.com/storage/management/performance/api/2005/08/vDiskStats}', vdisk.get('idx')))
                    if old_vdisk is not None:
                        ctw += int(vdisk.get('ctw')) - int(old_vdisk.get('ctw'))
                        ctwft += int(vdisk.get('ctwft')) - int(old_vdisk.get('ctwft'))
                        ctwwt += int(vdisk.get('ctwwt')) - int(old_vdisk.get('ctwwt'))
                if ctw > 0:
                    write_cache_delay_percentage = ( ctwft + ctwwt ) / ctw
                data[svc_cluster]['node.{}'.format(node_sysid)]['counter']['write_cache_delay_percentage'] = write_cache_delay_percentage










        ## Metrics for MDiskGrp    

        # read_data_rate : Nv file > vdsk > ctrs (512 bytes sector write)
        # read_io_rate : Nv file > vdsk > ro (read operation)
        # write_data_rate : Nv file > vdsk > ctws (512 bytes blocks write)
        # write_io_rate : Nv file > vdsk > wo (write operation)
            for vdisk in node_vdisks:
                mdiskGrp = vdiskList[vdisk.get('id')]['mdiskGrpName']
                data[svc_cluster]['mdsk.{}'.format(mdiskGrp)]['counter']['read_data_rate'] += (int(vdisk.get('rb')) * 512)
                data[svc_cluster]['mdsk.{}'.format(mdiskGrp)]['counter']['read_io_rate'] += int(vdisk.get('ro'))
                data[svc_cluster]['mdsk.{}'.format(mdiskGrp)]['counter']['write_data_rate'] += (int(vdisk.get('wb')) * 512)
                data[svc_cluster]['mdsk.{}'.format(mdiskGrp)]['counter']['write_io_rate'] += int(vdisk.get('wo'))

        # read_response_time : Nv file > vdsk > rl
        # write_response_time : Nv file > vdsk > wl
                if useOld == 1:
                    old_node_vdisks = old_stats[nodeId]['Nv'].findall('{http://ibm.com/storage/management/performance/api/2003/04/diskStats}vdsk')
                    for vdisk in node_vdisks:
                        if(vdisk.get('id') in vdiskList):
                            vdiskList[vdisk.get('id')]['ro'] += int(vdisk.get('ro'))
                            vdiskList[vdisk.get('id')]['wo']  += int(vdisk.get('wo'))
                            vdiskList[vdisk.get('id')]['rrp']  += int(vdisk.get('rl'))
                            vdiskList[vdisk.get('id')]['wrp']  += int(vdisk.get('wl'))
                    for vdisk in old_node_vdisks:
                        if(vdisk.get('id') in vdiskList):
                            vdiskList[vdisk.get('id')]['ro'] -= int(vdisk.get('ro'))
                            vdiskList[vdisk.get('id')]['wo']  -= int(vdisk.get('wo'))
                            vdiskList[vdisk.get('id')]['rrp']  -= int(vdisk.get('rl'))
                            vdiskList[vdisk.get('id')]['wrp']  -= int(vdisk.get('wl'))


        # backend_read_data_rate : Nm file > mdsk > rb (512 bytes blocks read)
        # backend_read_io_rate : Nm file > mdsk > ro (read operation)
        # backend_write_data_rate : Nm file > mdsk > wb (512 bytes blocks write)
        # backend_write_io_rate : Nm file > mdsk > wo (write operation) 
            for mdisk in node_mdisks:
                mdiskGrp = mdiskList[mdisk.get('id')]['mdiskGrpName']
                data[svc_cluster]['mdsk.{}'.format(mdiskGrp)]['counter']['backend_read_data_rate'] += (int(mdisk.get('rb')) * 512)
                data[svc_cluster]['mdsk.{}'.format(mdiskGrp)]['counter']['backend_read_io_rate'] += int(mdisk.get('ro'))
                data[svc_cluster]['mdsk.{}'.format(mdiskGrp)]['counter']['backend_write_data_rate'] += (int(mdisk.get('wb')) * 512)
                data[svc_cluster]['mdsk.{}'.format(mdiskGrp)]['counter']['backend_write_io_rate'] += int(mdisk.get('wo'))

        # backend_read_response_time
        # backend_write_response_time
                if useOld == 1:
                    old_node_mdisks = old_stats[nodeId]['Nm'].findall('{http://ibm.com/storage/management/performance/api/2003/04/diskStats}mdsk')
                    for mdisk in node_mdisks:
                        if(mdisk.get('id') in mdiskList):
                            mdiskList[mdisk.get('id')]['ro'] += int(mdisk.get('ro'))
                            mdiskList[mdisk.get('id')]['wo']  += int(mdisk.get('wo'))
                            mdiskList[mdisk.get('id')]['rrp']  += int(mdisk.get('re'))
                            mdiskList[mdisk.get('id')]['wrp']  += int(mdisk.get('we'))
                    for mdisk in old_node_mdisks:
                        if(mdisk.get('id') in mdiskList):
                            mdiskList[mdisk.get('id')]['ro'] -= int(mdisk.get('ro'))
                            mdiskList[mdisk.get('id')]['wo']  -= int(mdisk.get('wo'))
                            mdiskList[mdisk.get('id')]['rrp']  -= int(mdisk.get('re'))
                            mdiskList[mdisk.get('id')]['wrp']  -= int(mdisk.get('we'))










        ## Metrics for vdisks
        # read_io_rate Nv > vdsk > ro
        # write_io_rate Nv > vdsk > wo
        # read_data_rate Nv > vdsk > wo
        # write_data_rate Nv > vdsk > wo
        # read_response_time : Nv file > vdsk > rl
        # write_response_time : Nv file > vdsk > wl
            for vdisk in node_vdisks:
                data[svc_cluster]['vdsk.{}'.format(vdisk.get('id'))]['counter']['read_data_rate'] += int(vdisk.get('rb'))
                data[svc_cluster]['vdsk.{}'.format(vdisk.get('id'))]['counter']['write_data_rate'] += int(vdisk.get('wb'))
                data[svc_cluster]['vdsk.{}'.format(vdisk.get('id'))]['counter']['read_io_rate'] += int(vdisk.get('ro'))
                data[svc_cluster]['vdsk.{}'.format(vdisk.get('id'))]['counter']['write_io_rate'] += int(vdisk.get('wo'))










        # Frontend latency
        # Aggregate metrics of individual mdisks by mdiskGrp
        for vdisk in vdiskList: 
            mdiskGrpList[vdiskList[vdisk]['mdiskGrpName']]['ro'] += vdiskList[vdisk]['ro']
            mdiskGrpList[vdiskList[vdisk]['mdiskGrpName']]['wo'] += vdiskList[vdisk]['wo']
            mdiskGrpList[vdiskList[vdisk]['mdiskGrpName']]['rrp'] += vdiskList[vdisk]['rrp']
            mdiskGrpList[vdiskList[vdisk]['mdiskGrpName']]['wrp'] += vdiskList[vdisk]['wrp']
            if vdiskList[vdisk]['ro'] == 0:
                data[svc_cluster]['vdsk.{}'.format(vdisk)]['gauge']['read_response_time'] += float(0)
            else :
                data[svc_cluster]['vdsk.{}'.format(vdisk)]['gauge']['read_response_time'] += vdiskList[vdisk]['rrp'] / vdiskList[vdisk]['ro']
            if vdiskList[vdisk]['wo'] == 0:
                data[svc_cluster]['vdsk.{}'.format(vdisk)]['gauge']['write_response_time'] += float(0)
            else : 
                data[svc_cluster]['vdsk.{}'.format(vdisk)]['gauge']['write_response_time'] += vdiskList[vdisk]['wrp'] / vdiskList[vdisk]['wo']

        # Get average response time by IO (total response time / numbers of IO)
        for mdiskGrp in mdiskGrpList:
            if mdiskGrpList[mdiskGrp]['ro'] == 0: #avoid division by 0
                data[svc_cluster]['mdsk.{}'.format(mdiskGrp)]['gauge']['read_response_time'] += float(0)
            else :
                data[svc_cluster]['mdsk.{}'.format(mdiskGrp)]['gauge']['read_response_time'] += float(mdiskGrpList[mdiskGrp]['rrp']/mdiskGrpList[mdiskGrp]['ro'])
            if mdiskGrpList[mdiskGrp]['wo'] == 0: #avoid division by 0
                data[svc_cluster]['mdsk.{}'.format(mdiskGrp)]['gauge']['write_response_time'] += float(0)
            else :
                data[svc_cluster]['mdsk.{}'.format(mdiskGrp)]['gauge']['write_response_time'] += float(mdiskGrpList[mdiskGrp]['wrp']/mdiskGrpList[mdiskGrp]['wo'])


        # Backend latency
        # Aggregate metrics of individual mdisks by mdiskGrg
        for mdisk in mdiskList: 
            mdiskGrpList[mdiskList[mdisk]['mdiskGrpName']]['b_ro'] += mdiskList[mdisk]['ro']
            mdiskGrpList[mdiskList[mdisk]['mdiskGrpName']]['b_wo'] += mdiskList[mdisk]['wo']
            mdiskGrpList[mdiskList[mdisk]['mdiskGrpName']]['b_rrp'] += mdiskList[mdisk]['rrp']
            mdiskGrpList[mdiskList[mdisk]['mdiskGrpName']]['b_wrp'] += mdiskList[mdisk]['wrp']
        # Get average response time by IO (total response time / numbers of IO)
        for mdiskGrp in mdiskGrpList:
            if mdiskGrpList[mdiskGrp]['b_ro'] == 0: #avoid division by 0
                data[svc_cluster]['mdsk.{}'.format(mdiskGrp)]['gauge']['backend_read_response_time'] += float(0)
            else :
                data[svc_cluster]['mdsk.{}'.format(mdiskGrp)]['gauge']['backend_read_response_time'] += float(mdiskGrpList[mdiskGrp]['b_rrp']/mdiskGrpList[mdiskGrp]['b_ro'])
            if mdiskGrpList[mdiskGrp]['b_wo'] == 0: #avoid division by 0
                data[svc_cluster]['mdsk.{}'.format(mdiskGrp)]['gauge']['backend_write_response_time'] += float(0)
            else :
                data[svc_cluster]['mdsk.{}'.format(mdiskGrp)]['gauge']['backend_write_response_time'] += float(mdiskGrpList[mdiskGrp]['b_wrp']/mdiskGrpList[mdiskGrp]['b_wo'])










        ssh.close()
        return data

try:
    plugin = SVCPlugin()
except Exception as exc:
    collectd.error("svc-collect: failed to initialize svc-collect plugin :: %s :: %s"
            % (exc, traceback.format_exc()))

def configure_callback(conf):
    """Received configuration information"""
    plugin.config_callback(conf)
    collectd.register_read(read_callback, plugin.interval)

def read_callback():
    """Callback triggerred by collectd on read"""
    plugin.read_callback()

collectd.register_init(SVCPlugin.reset_sigchld)
collectd.register_config(configure_callback)
