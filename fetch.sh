while true; do
  eval " curl http://10.12.0.61/ 2> /dev/null | tee shotclock.html"
  sleep 0.5
done