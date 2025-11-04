# Migration Checklist

Use this checklist to migrate from the old Salt-based Saltpeter to the new WebSocket-based version.

## Pre-Migration

- [ ] **Backup current configuration**
  ```bash
  cp -r /etc/saltpeter /etc/saltpeter.backup.$(date +%Y%m%d)
  cp -r /var/log/saltpeter /var/log/saltpeter.backup.$(date +%Y%m%d)
  ```

- [ ] **Document current jobs**
  ```bash
  ls -la /etc/saltpeter/
  curl http://localhost:8888/config > config_backup.json
  ```

- [ ] **Verify Python version**
  ```bash
  python3 --version  # Should be 3.7+ (tested with 3.11)
  ```

- [ ] **Check available disk space**
  ```bash
  df -h /var/log/saltpeter
  df -h /etc/saltpeter
  ```

## Installation

- [ ] **Update code from repository**
  ```bash
  cd /home/marin/syscollective/saltpeter
  git pull origin async-agent
  ```

- [ ] **Install new dependencies**
  ```bash
  pip install -r requirements.txt
  # Verify websockets installed:
  python3 -c "import websockets; print('OK')"
  ```

- [ ] **Update Saltpeter package** (if installed via pip)
  ```bash
  pip install --upgrade .
  ```

## Firewall Configuration

- [ ] **Open WebSocket port on Saltpeter server**
  ```bash
  sudo firewall-cmd --add-port=8889/tcp --permanent
  sudo firewall-cmd --reload
  # Verify:
  sudo firewall-cmd --list-ports | grep 8889
  ```

- [ ] **Test connectivity from minions** (optional but recommended)
  ```bash
  salt '*' cmd.run 'telnet saltpeter_hostname 8889'
  ```

## Wrapper Deployment

Choose one deployment method:

### Option A: Salt File Server

- [ ] **Copy wrapper to Salt file server**
  ```bash
  sudo mkdir -p /srv/salt/saltpeter
  sudo cp saltpeter/wrapper.py /srv/salt/saltpeter/wrapper.py
  ```

- [ ] **Deploy to all minions**
  ```bash
  salt '*' cp.get_file salt://saltpeter/wrapper.py /usr/local/bin/saltpeter-wrapper.py
  salt '*' cmd.run 'chmod +x /usr/local/bin/saltpeter-wrapper.py'
  ```

- [ ] **Verify deployment**
  ```bash
  salt '*' cmd.run 'ls -l /usr/local/bin/saltpeter-wrapper.py'
  salt '*' cmd.run 'head -1 /usr/local/bin/saltpeter-wrapper.py'
  ```

### Option B: Package Installation

- [ ] **Build package with wrapper**
  ```bash
  python3 setup.py sdist
  ```

- [ ] **Install on minions**
  ```bash
  salt '*' cmd.run 'pip install saltpeter-<version>.tar.gz'
  ```

## Configuration Update

- [ ] **Add maintenance configuration** (optional)
  ```bash
  cat > /etc/saltpeter/maintenance.yaml << 'EOF'
  saltpeter_maintenance:
    global: false
    machines: []
  EOF
  ```

- [ ] **Review existing job timeouts**
  ```bash
  grep -r "timeout:" /etc/saltpeter/
  # Ensure timeouts are appropriate for job durations
  ```

- [ ] **Update job configurations** (if needed)
  - Review and adjust timeout values
  - Remove any Salt-specific workarounds

## Testing

- [ ] **Stop current Saltpeter instance**
  ```bash
  pkill -f saltpeter
  # Or if using systemd:
  sudo systemctl stop saltpeter
  ```

- [ ] **Start new Saltpeter with WebSocket**
  ```bash
  python3 -m saltpeter.main -w 8889 --websocket-host 0.0.0.0 -a -c /etc/saltpeter -l /var/log/saltpeter
  ```

- [ ] **Verify WebSocket server started**
  ```bash
  # Check logs for:
  # "WebSocket server started on ws://0.0.0.0:8889"
  netstat -an | grep 8889 | grep LISTEN
  ```

- [ ] **Run WebSocket test suite**
  ```bash
  python3 test_websocket.py ws://localhost:8889
  # Should show all tests passed
  ```

- [ ] **Create a test job**
  ```bash
  cat > /etc/saltpeter/test.yaml << 'EOF'
  test_migration:
    targets: '*'
    target_type: 'glob'
    command: 'echo "Migration test"; date; sleep 5; echo "Complete"'
    timeout: 60
    min: '*/10'
    hour: '*'
    dom: '*'
    mon: '*'
    dow: '*'
  EOF
  ```

- [ ] **Manually trigger test job**
  ```bash
  curl -X POST http://localhost:8888/runnow/test_migration
  ```

- [ ] **Monitor test job execution**
  ```bash
  # Watch logs for WebSocket messages
  tail -f /var/log/saltpeter/test_migration.log
  
  # Check for WebSocket activity
  grep -i websocket /var/log/saltpeter/*.log
  ```

- [ ] **Verify test job completed successfully**
  ```bash
  # Check job log
  cat /var/log/saltpeter/test_migration.log
  
  # Check via API
  curl http://localhost:8888/state | jq '.test_migration'
  ```

## Maintenance Mode Testing

- [ ] **Test global maintenance mode**
  ```bash
  # Edit maintenance.yaml
  cat > /etc/saltpeter/maintenance.yaml << 'EOF'
  saltpeter_maintenance:
    global: true
    machines: []
  EOF
  
  # Trigger a job - should be blocked
  curl -X POST http://localhost:8888/runnow/test_migration
  
  # Check logs for maintenance message
  grep -i maintenance /var/log/saltpeter/*.log
  ```

- [ ] **Test machine-level maintenance**
  ```bash
  # Edit maintenance.yaml
  cat > /etc/saltpeter/maintenance.yaml << 'EOF'
  saltpeter_maintenance:
    global: false
    machines:
      - minion1
  EOF
  
  # Trigger a job - minion1 should be excluded
  curl -X POST http://localhost:8888/runnow/test_migration
  
  # Verify minion1 was excluded
  grep -i "under maintenance" /var/log/saltpeter/test_migration.log
  ```

- [ ] **Disable maintenance mode**
  ```bash
  cat > /etc/saltpeter/maintenance.yaml << 'EOF'
  saltpeter_maintenance:
    global: false
    machines: []
  EOF
  ```

## Production Deployment

- [ ] **Update systemd service file** (if using systemd)
  ```bash
  sudo nano /etc/systemd/system/saltpeter.service
  
  # Update ExecStart to include WebSocket arguments:
  # ExecStart=/usr/bin/python3 -m saltpeter.main -w 8889 --websocket-host 0.0.0.0 -a
  
  sudo systemctl daemon-reload
  ```

- [ ] **Start Saltpeter service**
  ```bash
  sudo systemctl start saltpeter
  sudo systemctl enable saltpeter
  ```

- [ ] **Verify service status**
  ```bash
  sudo systemctl status saltpeter
  ```

- [ ] **Monitor for 24 hours**
  ```bash
  # Check logs regularly
  tail -f /var/log/saltpeter/*.log
  
  # Monitor WebSocket connections
  watch 'netstat -an | grep 8889'
  
  # Check for errors
  grep -i error /var/log/saltpeter/*.log
  ```

## Post-Migration Validation

- [ ] **Verify all jobs ran successfully**
  ```bash
  # Check recent job executions
  ls -lt /var/log/saltpeter/*.log | head -20
  
  # Review exit codes
  grep "Exit Code" /var/log/saltpeter/*.log | tail -50
  ```

- [ ] **Check for WebSocket errors**
  ```bash
  grep -i "websocket.*error" /var/log/saltpeter/*.log
  grep -i "connection.*closed" /var/log/saltpeter/*.log
  ```

- [ ] **Verify heartbeats are working**
  ```bash
  grep -i heartbeat /var/log/saltpeter/*.log | tail -20
  ```

- [ ] **Compare job execution times**
  ```bash
  # Compare with pre-migration logs
  # Jobs should complete in similar timeframes
  ```

- [ ] **Monitor system resources**
  ```bash
  # Check CPU/memory usage
  top -p $(pgrep -f saltpeter)
  
  # Check WebSocket connection count
  netstat -an | grep 8889 | grep ESTABLISHED | wc -l
  ```

## Rollback Plan (If Needed)

- [ ] **Stop new Saltpeter**
  ```bash
  sudo systemctl stop saltpeter
  # Or: pkill -f saltpeter
  ```

- [ ] **Restore old configuration**
  ```bash
  cp -r /etc/saltpeter.backup.YYYYMMDD/* /etc/saltpeter/
  ```

- [ ] **Install old version**
  ```bash
  # git checkout to previous commit
  # Or reinstall old package version
  ```

- [ ] **Start old Saltpeter**
  ```bash
  sudo systemctl start saltpeter
  ```

- [ ] **Document rollback reason**
  - Note specific issues encountered
  - Save relevant logs for debugging

## Cleanup

- [ ] **Remove test job configuration**
  ```bash
  rm /etc/saltpeter/test.yaml
  ```

- [ ] **Archive old logs** (after successful migration)
  ```bash
  tar -czf saltpeter_logs_pre_websocket_$(date +%Y%m%d).tar.gz /var/log/saltpeter.backup.*
  ```

- [ ] **Update documentation**
  - Document any custom configurations
  - Note any issues encountered during migration
  - Update runbooks/procedures

## Success Criteria

Migration is successful when:

- [ ] All scheduled jobs run and complete successfully
- [ ] WebSocket connections are established and maintained
- [ ] Heartbeats are being sent every 5 seconds
- [ ] Job output is being captured correctly
- [ ] Timeouts are handled appropriately
- [ ] No errors in logs for 24+ hours
- [ ] Maintenance mode functions as expected
- [ ] System resources (CPU/memory) are within normal ranges

## Notes

Use this section to document any issues, workarounds, or custom configurations:

```
Date: ___________
Migration performed by: ___________

Issues encountered:


Workarounds applied:


Custom configurations:


```

## Support Resources

- **Documentation:**
  - `WEBSOCKET_ARCHITECTURE.md` - Architecture details
  - `REFACTORING_SUMMARY.md` - Complete change summary
  - `QUICKSTART.md` - Quick start guide

- **Test Tools:**
  - `test_websocket.py` - WebSocket connectivity test

- **Examples:**
  - `examples/config/maintenance_example.yaml` - Maintenance configuration

- **Logs:**
  - `/var/log/saltpeter/*.log` - Job logs
  - System logs for Saltpeter service

---

**Migration Status:** ☐ Not Started | ☐ In Progress | ☐ Completed | ☐ Rolled Back

**Completion Date:** ___________

**Signed Off By:** ___________
