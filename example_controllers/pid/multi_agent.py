import threading
import multiprocessing
import tensorflow as tf
import example_controllers.pid.playGame_DDPG_pid as playGame_DDPG
from time import sleep, time

with tf.device("/cpu:0"):
        num_workers = 3  #  multiprocessing.cpu_count() #use this when you want to use all the cpus
        print("numb of workers is" + str(num_workers))

with tf.Session() as sess:
        worker_threads = []
#with pymp.Parallel(4) as p:		#uncomment this for parallelization of threads
        for i in range(num_workers):
                worker_work = lambda: (playGame_DDPG.playGame(f_diagnostics=""+str(i), train_indicator=1, port=3001+i))
                t = threading.Thread(target=(worker_work))
                t.start()
                sleep(0.5)
                worker_threads.append(t)
