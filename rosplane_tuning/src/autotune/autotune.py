#!/usr/bin/env python3

from rosplane_msgs.msg import ControllerCommands
from rosplane_msgs.msg import ControllerInternalsDebug
from rosplane_msgs.msg import State
from optimizer import Optimizer

import rclpy
from rclpy.executors import MultiThreadedExecutor
from rclpy.callback_groups import MutuallyExclusiveCallbackGroup
from rclpy.node import Node
from rclpy.parameter import Parameter
from rcl_interfaces.srv import GetParameters, SetParameters
from std_srvs.srv import Trigger

from enum import Enum, auto
import numpy as np
import time


class CurrentAutopilot(Enum):
    """
    This class defines which autopilots are available for tuning.
    """
    ROLL = auto()
    COURSE = auto()
    PITCH = auto()
    ALTITUDE = auto()
    AIRSPEED = auto()


class Autotune(Node):
    """
    This class is an auto-tuning node for the ROSplane autopilot. The node calculates the error
    between the state estimate of the system and the commanded setpoint for a given autopilot.
    A gradient-based optimization is then run to find the optimal gains to minimize the error.

    This class itself contains the ROS-specific code for the autotune node. The optimization
    algorithms are contained in the Optimizer class.

    Va is airspeed, phi is roll angle, chi is course angle, theta is pitch angle, and h is altitude.
    """

    def __init__(self):
        super().__init__('autotune')

        # Callback groups, used for allowing external services to run mid-internal callback
        self.internal_callback_group = MutuallyExclusiveCallbackGroup()
        self.external_callback_group = MutuallyExclusiveCallbackGroup()

        # Class state variables
        self.collecting_data = False

        # Data storage
        self.state = []
        self.commands = []
        self.internals_debug = []

        # ROS parameters
        # The amount of time to collect data for calculating the error
        self.declare_parameter('/autotune/stabilize_period', rclpy.Parameter.Type.DOUBLE)
        # The autopilot that is currently being tuned
        self.declare_parameter('/autotune/current_tuning_autopilot', rclpy.Parameter.Type.STRING)
        # Get the autopilot to tune
        if self.get_parameter('/autotune/current_tuning_autopilot').value == 'roll':
            self.current_autopilot = CurrentAutopilot.ROLL
        elif self.get_parameter('/autotune/current_tuning_autopilot').value == 'course':
            self.current_autopilot = CurrentAutopilot.COURSE
        elif self.get_parameter('/autotune/current_tuning_autopilot').value == 'pitch':
            self.current_autopilot = CurrentAutopilot.PITCH
        elif self.get_parameter('/autotune/current_tuning_autopilot').value == 'altitude':
            self.current_autopilot = CurrentAutopilot.ALTITUDE
        elif self.get_parameter('/autotune/current_tuning_autopilot').value == 'airspeed':
            self.current_autopilot = CurrentAutopilot.AIRSPEED
        else:
            self.get_logger().fatal(self.get_parameter('/autotune/current_tuning_autopilot').value +
                                    ' is not a valid value for current_tuning_autopilot.' +
                                    ' Please select one of the' +
                                    ' following: roll, course, pitch, altitude, airspeed.')
            rclpy.shutdown()

        # Subscriptions
        self.state_subscription = self.create_subscription(
            State,
            'estimated_state',
            self.state_callback,
            10,
            callback_group=self.internal_callback_group)
        self.commands_subscription = self.create_subscription(
            ControllerCommands,
            'controller_commands',
            self.commands_callback,
            10,
            callback_group=self.internal_callback_group)
        self.internals_debug_subscription = self.create_subscription(
            ControllerInternalsDebug,
            'tuning_debug',
            self.internals_debug_callback,
            10,
            callback_group=self.internal_callback_group)

        # Timers
        self.stabilize_period_timer = self.create_timer(
            self.get_parameter('/autotune/stabilize_period').value,
            self.stabilize_period_timer_callback,
            callback_group=self.internal_callback_group)
        self.stabilize_period_timer.cancel()

        # Services
        self.run_tuning_iteration_service = self.create_service(
            Trigger,
            '/autotune/run_tuning_iteration',
            self.run_tuning_iteration_callback,
            callback_group=self.internal_callback_group)

        # Clients
        self.toggle_step_signal_client = self.create_client(
                Trigger,
                '/autotune/toggle_step_signal',
                callback_group=self.external_callback_group)
        self.get_parameter_client = self.create_client(
                GetParameters,
                '/autopilot/get_parameters',
                callback_group=self.external_callback_group)
        self.set_parameter_client = self.create_client(
                SetParameters,
                '/autopilot/set_parameters',
                callback_group=self.external_callback_group)

        # Optimization
        self.new_gains = self.get_gains()
        self.optimizer = Optimizer(self.new_gains, {'u1': 10**-4, 'u2': 0.5, 'sigma': 1.5,
                                                    'alpha': 1, 'tau': 10**-3})


    ## ROS Callbacks ##
    def state_callback(self, msg):
        """
        This function is called when a new state estimate is received. It stores the state estimate
        if the node is currently collecting data.
        """

        if self.collecting_data:
            time = msg.header.stamp.sec + msg.header.stamp.nanosec * 1e-9
            self.state.append([time, msg.va, msg.phi, msg.chi, msg.theta, 
                               -msg.position[2]])  # h = -msg.position[2]

    def commands_callback(self, msg):
        """
        This function is called when new commands are received. It stores the commands if the node
        is currently collecting data.
        """

        if self.collecting_data:
            time = msg.header.stamp.sec + msg.header.stamp.nanosec * 1e-9
            self.commands.append([time, msg.va_c, msg.chi_c, msg.h_c])

    def internals_debug_callback(self, msg):
        """
        This function is called when new debug information is received. It stores the debug
        information if the node is currently collecting data.
        """

        if self.collecting_data:
            time = msg.header.stamp.sec + msg.header.stamp.nanosec * 1e-9
            self.internals_debug.append([time, msg.phi_c, msg.theta_c])

    def stabilize_period_timer_callback(self):
        """
        This function is called when the stability timer callback occurs. It starts/stops data
        collection and sets up ROSplane to perform a step manuever.
        """

        if not self.collecting_data:
            # Stabilization period is over, start collecting data
            self.get_logger().info('Stepping command and collecting data for '
                                   + str(self.get_parameter('stabilize_period').value)
                                   + ' seconds...')
            self.collecting_data = True
            self.call_toggle_step_signal()
        else:
            # Data collection is over, stop collecting data and calculate gains for next iteration
            self.get_logger().info('Data collection complete.')
            self.collecting_data = False
            self.stabilize_period_timer.cancel()
            self.call_toggle_step_signal()
            self.new_gains = self.optimizer.get_next_parameter_set(self.calculate_error())


    def run_tuning_iteration_callback(self, request, response):
        """
        This function is called when the run_tuning_iteration service is called. It starts the
        next iteration of the optimization process.
        """

        if not self.optimizer.optimization_terminated():
            self.get_logger().info('Setting gains: ' + str(self.new_gains))
            self.set_gains(self.new_gains)

            self.stabilize_period_timer.timer_period_ns = \
                    int(self.get_parameter('stabilize_period').value * 1e9)
            self.stabilize_period_timer.reset()

            self.get_logger().info('Stabilizing autopilot for '
                                   + str(self.get_parameter('stabilize_period').value)
                                   + ' seconds...')

        response.success = True
        response.message = self.optimizer.get_optimiztion_status()

        return response


    ## Helper Functions ##
    def call_toggle_step_signal(self):
        """
        Call the signal_generator's toggle step service to toggle the step input.
        """

        while not self.toggle_step_signal_client.wait_for_service(timeout_sec=1.0):
            self.get_logger().info(f'Service {self.toggle_step_signal_client.srv_name} ' +
            'not available, waiting...')

        request = Trigger.Request()
        self.toggle_step_signal_client.call_async(request)

    def get_gains(self):
        """
        Gets the current gains of the autopilot.

        Returns:
        list of floats: The current gains of the autopilot that is being tuning.
        """
        request = GetParameters.Request()

        while not self.get_parameter_client.wait_for_service(timeout_sec=1.0):
            self.get_logger().info(f'Service {self.get_parameter_client.srv_name}' +
                                   ' not available, waiting...')

        if self.current_autopilot == CurrentAutopilot.ROLL:
            request.names = ['r_kp', 'r_kd']
        elif self.current_autopilot == CurrentAutopilot.COURSE:
            request.names = ['c_kp', 'c_ki']
        elif self.current_autopilot == CurrentAutopilot.PITCH:
            request.names = ['p_kp', 'p_kd']
        elif self.current_autopilot == CurrentAutopilot.ALTITUDE:
            request.names = ['a_kp', 'a_ki']
        else:  # CurrentAutopilot.AIRSPEED
            request.names = ['a_t_kp', 'a_t_ki']

        self.get_parameter_client.call_async(request)

        return np.array([1.0, 1.0])  # Placeholder

    def set_gains(self, gains):
        """
        Set the gains of the autopilot to the given values.
        """

        request = SetParameters.Request()
        if self.current_autopilot == CurrentAutopilot.ROLL:
            request.parameters = [Parameter(name='r_kp', value=gains[0]).to_parameter_msg(),
                                  Parameter(name='r_kd', value=gains[1]).to_parameter_msg()]
        elif self.current_autopilot == CurrentAutopilot.COURSE:
            request.parameters = [Parameter(name='c_kp', value=gains[0]).to_parameter_msg(),
                                  Parameter(name='c_ki', value=gains[1]).to_parameter_msg()]
        elif self.current_autopilot == CurrentAutopilot.PITCH:
            request.parameters = [Parameter(name='p_kp', value=gains[0]).to_parameter_msg(),
                                  Parameter(name='p_kd', value=gains[1]).to_parameter_msg()]
        elif self.current_autopilot == CurrentAutopilot.ALTITUDE:
            request.parameters = [Parameter(name='a_kp', value=gains[0]).to_parameter_msg(),
                                  Parameter(name='a_ki', value=gains[1]).to_parameter_msg()]
        else:  # CurrentAutopilot.AIRSPEED
            request.parameters = [Parameter(name='a_t_kp', value=gains[0]).to_parameter_msg(),
                                  Parameter(name='a_t_ki', value=gains[1]).to_parameter_msg()]

        # Call the service
        while not self.set_parameter_client.wait_for_service(timeout_sec=1.0):
            self.get_logger().info('Service {self.set_parameter_client.srv_name}' +
                                   ' not available, waiting...')
        future = self.set_parameter_client.call_async(request)

        # Wait for the service to complete, exiting if it takes too long
        # rclcpp has a function for this, but I couldn't seem to find one for rclpy
        call_time = time.time()
        callback_complete = False
        while call_time + 5 > time.time():
            if future.done():
                callback_complete = True
                break
        if not callback_complete:
            self.get_logger().error('Unable to set autopilot gains after 5 seconds.')

        # Print any errors that occurred
        for response in future.result().results:
            if not response.successful:
                self.get_logger().error(f'Failed to set parameter: {response.reason}')


    def calculate_error(self):
        """
        Calculate the error between the state estimate and the commanded setpoint using the
        collected data.
        """
        # TODO: Implement this function
        pass


def main(args=None):
    rclpy.init(args=args)

    autotune = Autotune()
    executor = MultiThreadedExecutor()
    executor.add_node(autotune)
    executor.spin()

    autotune.destroy_node()
    rclpy.shutdown()


if __name__ == '__main__':
    main()

