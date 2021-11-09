# Copyright (c) # Copyright (c) 2018-2020 CVC.
#
# This work is licensed under the terms of the MIT license.
# For a copy, see <https://opensource.org/licenses/MIT>.

"""
This module implements an agent that roams around a track following random
waypoints and avoiding other vehicles. The agent also responds to traffic lights.
It can also make use of the global route planner to follow a specifed route
"""

import carla
import weakref

from agents.navigation.basic_agent import BasicAgent
from agents.tools.misc import get_speed

class ConstantVelocityAgent(BasicAgent):
    """
    ConstantVelocityAgent implements an agent that navigates the scene at a fixed velocity.
    This agent will fail if asked to perform turns that are impossible are the desired speed.
    This includes lane changes. When a collision is detected, the constant velocity will stop,
    and normal behavior.
    """

    def __init__(self, vehicle, target_speed=20, opt_dict={}):
        """
        Initialization the agent parameters, the local and the global planner.

            :param vehicle: actor to apply to agent logic onto
            :param target_speed: speed (in Km/h) at which the vehicle will move
            :param opt_dict: dictionary in case some of its parameters want to be changed.
                This also applies to parameters related to the LocalPlanner.
        """
        super(ConstantVelocityAgent, self).__init__(vehicle, target_speed, opt_dict=opt_dict)

        self._restart_time = 10  # Time after collision before the constant velocity behavior starts again
        self._use_basic_behavior = True  # Whether or not to use the BasicAgent behavior when the constant velocity is down
        self._acceleration = 3.5 # [m/s^2]
        self._max_speed = target_speed / 3.6

        if 'restart_time' in opt_dict:
            self._restart_time = opt_dict['restart_time']
        if 'use_basic_behavior' in opt_dict:
            self._use_basic_behavior = opt_dict['use_basic_behavior']
        if 'acceleration' in opt_dict:
            self._acceleration = opt_dict['acceleration']
        self._constant_velocity_stop_time = None

        self._set_collision_sensor()
        self._set_constant_velocity()

    def set_target_speed(self, speed):
        """Changes the target speed of the agent"""
        super(ConstantVelocityAgent, self).set_target_speed(speed)
        self._set_constant_velocity()

    def _set_constant_velocity(self):
        """Forces the agent to drive at the specified speed"""
        self._vehicle.enable_constant_velocity(carla.Vector3D(self._target_speed / 3.6, 0, 0))
        self.is_constant_velocity_active = True

    def _stop_vehicle(self, speed):
        """Stops the vehicle"""
        new_speed = max(speed - self._acceleration, 0)
        self._vehicle.enable_constant_velocity(carla.Vector3D(new_speed, 0, 0))

    def _stop_constant_velocity(self):
        """Stops the constant velocity behavior"""
        self._vehicle.disable_constant_velocity()

    def restart_constant_velocity(self):
        """Public method to restart the constant velocity"""
        self._set_constant_velocity()

    def run_step(self):
        """Execute one step of navigation."""
        if not self.is_constant_velocity_active:
            # Check if it time to restart it again. If not, do something else
            if self._world.get_snapshot().timestamp.elapsed_seconds - self._constant_velocity_stop_time > self._restart_time:
                self.restart_constant_velocity()
                self.is_constant_velocity_active = True
            elif self._use_basic_behavior:
                return super(ConstantVelocityAgent, self).run_step()
            else:
                return carla.VehicleControl()

        vehicle_location = self._vehicle.get_location()
        vehicle_wp = self._map.get_waypoint(vehicle_location)
        if abs(vehicle_wp.transform.location.z - vehicle_location.z) > 5:
            self._stop_constant_velocity()

        hazard_detected = False

        # Retrieve all relevant actors
        actor_list = self._world.get_actors()
        vehicle_list = actor_list.filter("*vehicle*")
        lights_list = actor_list.filter("*traffic_light*")

        vehicle_speed = get_speed(self._vehicle) / 3.6

        max_vehicle_distance = self._base_vehicle_threshold + vehicle_speed
        affected_by_vehicle, _, _ = self._vehicle_obstacle_detected(vehicle_list, max_vehicle_distance)
        if affected_by_vehicle:
            hazard_detected = True

        # Check if the vehicle is affected by a red traffic light
        max_tlight_distance = self._base_tlight_threshold + 0.3 * vehicle_speed
        affected_by_tlight, _ = self._affected_by_traffic_light(lights_list, max_tlight_distance)
        if affected_by_tlight:
            hazard_detected = True

        # The longitudinal PID is overwritten by the constant velocity, so set them to 0 (though it really isn't needed)
        control = self._local_planner.run_step()
        control.throttle = 0
        control.brake = 0

        if hazard_detected:
            new_speed = max(vehicle_speed - self._acceleration, 0)
        else:
            new_speed = min(vehicle_speed + self._acceleration, self._max_speed)
        self._vehicle.enable_constant_velocity(carla.Vector3D(new_speed, 0, 0))

        return control

    def _set_collision_sensor(self):
        blueprint = self._world.get_blueprint_library().find('sensor.other.collision')
        self._collision_sensor = self._world.spawn_actor(blueprint, carla.Transform(), attach_to=self._vehicle)
        self._collision_sensor.listen(lambda event: self._on_collision(weakref.ref(self), event))

    @staticmethod
    def _on_collision(weak_self, event):
        """Disables the constant velocity to avoid weird behaviors"""
        self = weak_self()
        self.is_constant_velocity_active = False
        self._stop_constant_velocity()
        self._constant_velocity_stop_time =  self._world.get_snapshot().timestamp.elapsed_seconds