#include "controller_example.hpp"

#include "iostream"

namespace rosplane2
{

controller_example::controller_example() : controller_state_machine()
{

  // Initialize course hold, roll hold and pitch hold errors and integrators to zero.
  c_error_ = 0;
  c_integrator_ = 0;
  r_error_ = 0;
  r_integrator = 0;
  p_error_ = 0;
  p_integrator_ = 0;

}

void controller_example::take_off(const struct params_s &params, const struct input_s &input, struct output_s &output)
{

  // In the take-off zone maintain level straight flight by commanding a roll angle of 0 and rudder of 0.
  output.delta_r = 0;
  output.phi_c = 0;
  output.delta_a = roll_hold(output.phi_c, input.phi, input.p, params, input.Ts);

  // Set throttle to not overshoot altitude.
  output.delta_t = sat(airspeed_with_throttle_hold(input.Va_c, input.va, params, input.Ts), params.max_takeoff_throttle, 0);

  // Command a shallow pitch angle to gain altitude.
  output.theta_c = 3.0 * 3.14 / 180.0;
  output.delta_e = pitch_hold(output.theta_c, input.theta, input.q, params, input.Ts);

}

void controller_example::take_off_exit()
{
  // Put any code that should run as the airplane exits take off mode.
}

void controller_example::climb(const struct params_s &params, const struct input_s &input, struct output_s &output)
{
  double adjusted_hc;
  double max_alt;

  // Set the commanded altitude to a maximum of half the size of the altitude hold zone. Using adjusted_hc.
  max_alt = params.alt_hz/2.0;

  // If the error in altitude is larger than the max altitude, adjust it to the max with the correct sign.
  // Otherwise, proceed as normal.
  if (abs(input.h_c - input.h) > max_alt){
    adjusted_hc = input.h + copysign(max_alt, input.h_c - input.h);
  }
  else{
    adjusted_hc = input.h_c;
  }

  // Find the control efforts for throttle and find the commanded pitch angle.
  output.delta_t = airspeed_with_throttle_hold(input.Va_c, input.va, params, input.Ts);
  output.theta_c = altitude_hold_control(adjusted_hc, input.h, params, input.Ts);
  output.delta_e = pitch_hold(output.theta_c, input.theta, input.q, params, input.Ts);

  // Maintain straight flight while gaining altitude.
  output.phi_c = 0;
  output.delta_a = roll_hold(output.phi_c, input.phi, input.p, params, input.Ts);
  output.delta_r = 0;
}

void controller_example::climb_exit()
{
  // Reset differentiators, integrators and errors.
  at_error_ = 0;
  at_integrator_ = 0;
  at_differentiator_ = 0;
  a_error_ = 0;
  a_integrator_ = 0;
  a_differentiator_ = 0;
}

void controller_example::altitude_hold(const struct params_s &params, const struct input_s &input, struct output_s &output)
{
  double adjusted_hc;
  double max_alt;

  max_alt = params.alt_hz;

  // Adjust the altitude command if too large or too small to a maximum or minimum value. Otherwise,
  // continue as normal.
  if (abs(input.h_c - input.h) > max_alt){
    adjusted_hc = input.h + copysign(max_alt, input.h_c - input.h);
  }
  else{
    adjusted_hc = input.h_c;
  }

  // calculate the control effort to maintain airspeed and the required pitch angle to maintain altitude.
  output.delta_t = airspeed_with_throttle_hold(input.Va_c, input.va, params, input.Ts);
  output.theta_c = altitude_hold_control(adjusted_hc, input.h, params, input.Ts);

  // Set rudder command to zero, can use cooridinated_turn_hold if implemented.
  // Find commanded roll angle in order to achieve commanded course.
  // Find aileron deflection required to acheive required roll angle.
  output.delta_r = 0; //cooridinated_turn_hold(input.beta, params, input.Ts)
  output.phi_c = course_hold(input.chi_c, input.chi, input.phi_ff, input.r, params, input.Ts);
  output.delta_a = roll_hold(output.phi_c, input.phi, input.p, params, input.Ts);

  output.delta_e = pitch_hold(output.theta_c, input.theta, input.q, params, input.Ts);
}

void controller_example::altitude_hold_exit()
{
  c_integrator_ = 0;
}

/// All the following control loops follow this basic outline.
/*
    float controller_example::pid_control(float command_val, float actual_val, float rate, // Not all loops use rate.
                                          const params_s &params, float Ts)
    {
      // Find the error between the commanded and actual value.
      float error = commanded_val - actual_val;

      // Integrate the error of the state by using the trapezoid method with the stored value for the previous error.
      state_integrator_ = state_integrator_ + (Ts/2.0)*(error + state_error_);

      // Take the derivative of the error, using a dirty derivative with low pass filter value of tau.
      state_differentiator_ = (2.0*params.tau - Ts)/(2.0*params.tau + Ts)*state_differentiator_ + (2.0 /
                       (2.0*params.tau + Ts))*(error - state_error_);

      // Find the control efforts using the gains and calculated values.
      float up = params.state_kp*error;
      float ui = params.state_ki*state_integrator_;
      float ud = params.state_kd*rate; // If the rate is directly measured use it. Otherwise...
      // float ud = params.state_kd*state_differentiator;

      // Saturate the control effort between a defined max and min value.
      // If the saturation occurs, and you are using integral control, adjust the integrator.
      float control_effort = sat(up + ui + ud, max_value, min_value);
      if (fabs(params.c_ki) >= 0.00001)
      {
        float control_effort_unsat = up + ui + ud + phi_ff;
        state_integrator_ = state_integrator_ + (Ts/params.state_ki)*(control_effort - control_effort_unsat);
      }

      // Save the error to use for integration and differentiation.
      // Then return the control effort.
      state_error_ = error;
      return control_effort;
    }
*/

float controller_example::course_hold(float chi_c, float chi, float phi_ff, float r, const params_s &params, float Ts)
{
  float error = chi_c - chi;

  c_integrator_ = c_integrator_ + (Ts/2.0)*(error + c_error_);

  float up = params.c_kp*error;
  float ui = params.c_ki*c_integrator_;
  float ud = params.c_kd*r;

  float phi_c = sat(up + ui + ud + phi_ff, 15.0*3.14/180.0, -15.0*3.14/180.0);
  if (fabs(params.c_ki) >= 0.00001)
  {
    float phi_c_unsat = up + ui + ud + phi_ff;
    c_integrator_ = c_integrator_ + (Ts/params.c_ki)*(phi_c - phi_c_unsat);
  }

  c_error_ = error;
  return phi_c;
}

float controller_example::roll_hold(float phi_c, float phi, float p, const params_s &params, float Ts)
{
  float error = phi_c - phi;

  r_integrator = r_integrator + (Ts/2.0)*(error + r_error_);

  float up = params.r_kp*error;
  float ui = params.r_ki*r_integrator;
  float ud = params.r_kd*p;

  float delta_a = sat(up + ui - ud, params.max_a, -params.max_a);
  if (fabs(params.r_ki) >= 0.00001)
  {
    float delta_a_unsat = up + ui - ud;
    r_integrator = r_integrator + (Ts/params.r_ki)*(delta_a - delta_a_unsat);
  }

  r_error_ = error;
  return delta_a;
}

float controller_example::pitch_hold(float theta_c, float theta, float q, const params_s &params, float Ts)
{
  float error = theta_c - theta;

  p_integrator_ = p_integrator_ + (Ts/2.0)*(error + p_error_);

  float up = params.p_kp*error;
  float ui = params.p_ki*p_integrator_;
  float ud = params.p_kd*q;


  float delta_e = sat(params.trim_e/params.pwm_rad_e + up + ui - ud, params.max_e, -params.max_e);


  if (fabs(params.p_ki) >= 0.00001)
  {
    float delta_e_unsat = params.trim_e/params.pwm_rad_e + up + ui - ud;
    p_integrator_ = p_integrator_ + (Ts/params.p_ki)*(delta_e - delta_e_unsat);
  }

  p_error_ = error;
  return -delta_e; // TODO explain subtraction.
}

float controller_example::airspeed_with_throttle_hold(float Va_c, float Va, const params_s &params, float Ts)
{
  float error = Va_c - Va;

  at_integrator_ = at_integrator_ + (Ts/2.0)*(error + at_error_);
  at_differentiator_ = (2.0*params.tau - Ts)/(2.0*params.tau + Ts)*at_differentiator_ + (2.0 /
                       (2.0*params.tau + Ts))*(error - at_error_);

  float up = params.a_t_kp*error;
  float ui = params.a_t_ki*at_integrator_;
  float ud = params.a_t_kd*at_differentiator_;

  float delta_t = sat(params.trim_t + up + ui + ud, params.max_t, 0);
  if (fabs(params.a_t_ki) >= 0.00001)
  {
    float delta_t_unsat = params.trim_t + up + ui + ud;
    at_integrator_ = at_integrator_ + (Ts/params.a_t_ki)*(delta_t - delta_t_unsat);
  }

  at_error_ = error;
  return delta_t;
}

float controller_example::altitude_hold_control(float h_c, float h, const params_s &params, float Ts)
{
  float error = h_c - h;

  if (-params.alt_hz + .01 < error && error < params.alt_hz - .01) {
    a_integrator_ = a_integrator_ + (Ts / 2.0) * (error + a_error_);
  }
  else{
    a_integrator_ = 0.0;
  }

  a_differentiator_ = (2.0*params.tau - Ts)/(2.0*params.tau + Ts)*a_differentiator_ + (2.0 /
                      (2.0*params.tau + Ts))*(error - a_error_);

  float up = params.a_kp*error;
  float ui = params.a_ki*a_integrator_;
  float ud = params.a_kd*a_differentiator_;

  float theta_c = sat(up + ui + ud, 10.0*3.14/180.0, -10.0*3.14/180.0);
  if (fabs(params.a_ki) >= 0.00001)
  {
    float theta_c_unsat = up + ui + ud;
    a_integrator_ = a_integrator_ + (Ts/params.a_ki)*(theta_c - theta_c_unsat);
  }


  at_error_ = error;
  return theta_c;
}

//float controller_example::cooridinated_turn_hold(float v, const params_s &params, float Ts)
//{
//    //todo finish this if you want...
//    return 0;
//}

float controller_example::sat(float value, float up_limit, float low_limit)
{
  // Set to upper limit if larger than that limit.
  // Set to lower limit if smaller than that limit.
  // Otherwise, do not change the value.
  float rVal;
  if (value > up_limit)
    rVal = up_limit;
  else if (value < low_limit)
    rVal = low_limit;
  else
    rVal = value;

  // Return the saturated value.
  return rVal;
}

} //end namespace
