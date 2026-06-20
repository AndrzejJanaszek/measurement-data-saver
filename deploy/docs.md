
### status usługi
sudo systemctl status measurement-data-saver.service

### logi
sudo journalctl -u measurement-data-saver.service -f

### sterowanie servisem
sudo systemctl stop measurement-data-saver.service
sudo systemctl start measurement-data-saver.service
sudo systemctl restart measurement-data-saver.service