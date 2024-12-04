"""
Gym interface to snakeoil3_gym.py.

Provides a gym interface to the traditional server-client model.
"""
import os
import collections as col
from gym import spaces
import numpy as np
import copy
import utils.snakeoil3_gym as snakeoil3
from utils.madras_datatypes import Madras

madras = Madras()


class TorcsEnv:
    terminal_judge_start = 100      # If after 100 timestep still no progress, terminated
    termination_limit_progress = 1  # [km/h], episode terminates if car is running slower than this limit
    default_speed = 50
    initial_reset = False

    def __init__(self, vision=False, throttle=False, gear_change=False, obs_dim=29, act_dim=3):
        self.vision = vision
        self.throttle = throttle
        self.gear_change = gear_change
        self.obs_dim = obs_dim
        self.act_dim = act_dim

        self.initial_run = True
        self.time_step = 0

        self.currState = None 

        if throttle is False:                           # Throttle is generally True
            self.action_space = spaces.Box(low=-1.0, high=1.0, shape=(1,))
        else:
            high = np.array([1., 1., 1.], dtype=madras.floatX)
            low = np.array([-1., 0., 0.], dtype=madras.floatX)
            self.action_space = spaces.Box(low=low, high=high)    # steer,accel,brake

        if vision is False:                             # Vision has to be set True if you need the images from the simulator 
            high = np.inf * np.ones(self.obs_dim)
            low = -high
            self.observation_space = spaces.Box(low, high)
        else:
            high = np.array([1., np.inf, np.inf, np.inf, 1., np.inf, 1., np.inf, 255], dtype=madras.floatX)
            low = np.array([0., -np.inf, -np.inf, -np.inf, 0., -np.inf, 0., -np.inf, 0], dtype=madras.floatX)
            self.observation_space = spaces.Box(low=low, high=high)


    def terminate(self):
        episode_terminate = True
        # client.R.d['meta'] = True
        print('Terminating because bad episode')


    def step(self, step, client, u, early_stop=1):
        this_action = self.agent_to_torcs(u)

        # Apply Action
        action_torcs = client.R.d
        # Steering
        action_torcs['steer'] = this_action['steer']  # in [-1, 1]

        #  Simple Automatic Throttle Control by Snakeoil
        if self.throttle is False:
            target_speed = self.default_speed
            if client.S.d['speedX'] < target_speed - (client.R.d['steer']*50):
                client.R.d['accel'] += .01
            else:
                client.R.d['accel'] -= .01

            if client.R.d['accel'] > 0.2:
                client.R.d['accel'] = 0.2

            if client.S.d['speedX'] < 10:
                client.R.d['accel'] += 1/(client.S.d['speedX']+.1)

            # Traction Control System
            if ((client.S.d['wheelSpinVel'][2]+client.S.d['wheelSpinVel'][3]) -
               (client.S.d['wheelSpinVel'][0]+client.S.d['wheelSpinVel'][1]) > 5):
                action_torcs['accel'] -= .2
        else:
            action_torcs['accel'] = this_action['accel']
            action_torcs['brake'] = this_action['brake']

        #  Automatic Gear Change by Snakeoil
        if self.gear_change is True:
            action_torcs['gear'] = this_action['gear']
        else:
            #  Automatic Gear Change by Snakeoil is possible
            action_torcs['gear'] = 1
            if self.throttle:
                if client.S.d['speedX'] > 50:
                    action_torcs['gear'] = 2
                if client.S.d['speedX'] > 80:
                    action_torcs['gear'] = 3
                if client.S.d['speedX'] > 110:
                    action_torcs['gear'] = 4
                if client.S.d['speedX'] > 140:
                    action_torcs['gear'] = 5
                if client.S.d['speedX'] > 170:
                    action_torcs['gear'] = 6

        # Save the previous full-obs from torcs for the reward calculation
        obs_pre = copy.deepcopy(client.S.d)

        # One-Step Dynamics Update #################################
        # Apply the Agent's action into torcs
        client.respond_to_server()
        # Get the response of TORCS
        code = client.get_servers_input(step)

        if code==-1:
            client.R.d['meta'] = True
            print('Terminating because server stopped responding')
            return None, 0, client.R.d['meta'], {'termination_cause':'hardReset'}

        # Get the current full-observation from torcs
        obs = client.S.d

        # Make an obsevation from a raw observation vector from TORCS
        self.observation = self.make_observation(obs)
        self.currState = np.hstack((self.observation.angle, self.observation.track, self.observation.trackPos, 
                                    self.observation.speedX, self.observation.speedY,  self.observation.speedZ, 
                                    self.observation.wheelSpinVel/100.0, self.observation.rpm,self.observation.fuel))

        # direction-dependent positive reward
        track = np.array(obs['track'])
        trackPos = np.array(obs['trackPos'])
        sp = np.array(obs['speedX'])
        damage = np.array(obs['damage'])
        rpm = np.array(obs['rpm'])
        fuel_before = np.array(obs_pre['fuel'])
        fuel_after = np.array(obs['fuel'])
        fuel_consumed = fuel_before - fuel_after
        progress = sp * np.cos(obs['angle']) - np.abs(sp * np.sin(obs['angle'])) - sp * np.abs(obs['trackPos'])
        progress += 1/ (fuel_consumed**2 + 0.01)
        reward = progress

        # collision detection
        if obs['damage'] - obs_pre['damage'] > 0:
            reward = -1000

        # Termination judgement #########################
        episode_terminate = False
        if ((abs(track.any()) > 1 or abs(trackPos) > 1) and early_stop):  # Episode is terminated if the car is out of track
            reward = -200
            episode_terminate = True
            client.R.d['meta'] = True
            print('Terminating because Out of Track')

        if np.cos(obs['angle']) < 0:
            # Episode is terminated if the agent runs backward
            episode_terminate = True
            client.R.d['meta'] = True
            print('Terminating because agent Turned Back')


        if client.R.d['meta'] is True: # Send a reset signal
            self.initial_run = False
            client.respond_to_server()

        self.time_step += 1

        return self.observation, reward, client.R.d['meta'], {}


    def reset(self, client, relaunch=False):

        port = client.port
        self.time_step = 0

        if self.initial_reset is not True:
            client.R.d['meta'] = True
            client.respond_to_server()

            ## TENTATIVE. Restarting TORCS every episode suffers the memory leak bug!
            if relaunch is True:
                self.reset_torcs()
                print("### TORCS is RELAUNCHED ###")

        # Modify here if you use multiple tracks in the environment
        client = snakeoil3.Client(p=port, vision=self.vision)  # Open new UDP in vtorcs
        client.MAX_STEPS = np.inf

        # client = self.client
        client.get_servers_input(-1)  # Get the initial input from torcs

        obs = client.S.d  # Get the current full-observation from torcs
        self.observation = self.make_observation(obs)
        self.currState = np.hstack((self.observation.angle, self.observation.track, self.observation.trackPos, 
                                    self.observation.speedX, self.observation.speedY,  self.observation.speedZ, 
                                    self.observation.wheelSpinVel/100.0, self.observation.rpm , self.observation.fuel))

        self.last_u = None

        self.initial_reset = False
        return self.get_obs(), client

    def end(self):
        os.system('pkill torcs')

    def get_obs(self):
        return self.observation

    def reset_torcs(self):
        print("relaunch torcs")
        os.system('pkill torcs')

    def agent_to_torcs(self, u):
        torcs_action = {'steer': u[0]}

        if self.throttle is True:  # throttle action is enabled             
            torcs_action.update({'accel': u[1]})
            torcs_action.update({'brake': u[2]})

        if self.gear_change is True: # gear change action is enabled       
            torcs_action.update({'gear': int(u[3])})

        return torcs_action


    def obs_vision_to_image_rgb(self, obs_image_vec):
        image_vec =  obs_image_vec
        r = image_vec[0:len(image_vec):3]
        g = image_vec[1:len(image_vec):3]
        b = image_vec[2:len(image_vec):3]

        sz = (64, 64)
        r = np.array(r).reshape(sz)
        g = np.array(g).reshape(sz)
        b = np.array(b).reshape(sz)
        return np.array([r, g, b], dtype=np.uint8)

    def make_observation(self, raw_obs):
        if self.vision is False:
            names = ['focus',
                     'speedX', 'speedY', 'speedZ', 'angle', 'damage',
                     'opponents',
                     'rpm',
                     'track', 
                     'trackPos',
                     'wheelSpinVel',
                     'fuel']
            Observation = col.namedtuple('Observaion', names)
            return Observation(focus=np.array(raw_obs['focus'], dtype=madras.floatX)/200.,
                               speedX=np.array(raw_obs['speedX'], dtype=madras.floatX)/300.0,
                               speedY=np.array(raw_obs['speedY'], dtype=madras.floatX)/300.0,
                               speedZ=np.array(raw_obs['speedZ'], dtype=madras.floatX)/300.0,
                               angle=np.array(raw_obs['angle'], dtype=madras.floatX)/3.1416,
                               damage=np.array(raw_obs['damage'], dtype=madras.floatX),
                               opponents=np.array(raw_obs['opponents'], dtype=madras.floatX)/200.,
                               rpm=np.array(raw_obs['rpm'], dtype=madras.floatX)/10000,
                               track=np.array(raw_obs['track'], dtype=madras.floatX)/200.,
                               trackPos=np.array(raw_obs['trackPos'], dtype=madras.floatX)/1.,
                               wheelSpinVel=np.array(raw_obs['wheelSpinVel'], dtype=madras.floatX),
                               fuel = np.array(raw_obs['fuel'],dtype = madras.floatX))
        else:
            names = ['focus',
                     'speedX', 'speedY', 'speedZ', 'angle',
                     'opponents',
                     'rpm',
                     'track',
                     'trackPos',
                     'wheelSpinVel',
                     'img',
                     'fuel']
            Observation = col.namedtuple('Observaion', names)

            # Get RGB from observation
            image_rgb = self.obs_vision_to_image_rgb(raw_obs[names[8]])

            return Observation(focus=np.array(raw_obs['focus'], dtype=madras.floatX)/200.,
                               speedX=np.array(raw_obs['speedX'], dtype=madras.floatX)/self.default_speed,
                               speedY=np.array(raw_obs['speedY'], dtype=madras.floatX)/self.default_speed,
                               speedZ=np.array(raw_obs['speedZ'], dtype=madras.floatX)/self.default_speed,
                               opponents=np.array(raw_obs['opponents'], dtype=madras.floatX)/200.,
                               rpm=np.array(raw_obs['rpm'], dtype=madras.floatX),
                               track=np.array(raw_obs['track'], dtype=madras.floatX)/200.,
                               trackPos=np.array(raw_obs['trackPos'], dtype=madras.floatX)/1.,
                               wheelSpinVel=np.array(raw_obs['wheelSpinVel'], dtype=madras.floatX),
                               img=image_rgb,
                               fuel = np.array(raw_obs['fuel'],dtype = madras.floatX))
