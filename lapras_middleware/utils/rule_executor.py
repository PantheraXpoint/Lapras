# lapras_middleware/rule_executor.py
import logging
import time
import json
import threading # Not directly used in this simplified version but often present
from typing import Dict, Any, Optional, List, Union # Ensure Union/Optional are imported

import paho.mqtt.client as mqtt
import rdflib
from rdflib import Graph, Literal, Namespace
from rdflib.namespace import RDF, RDFS, XSD

logger = logging.getLogger(__name__)

class RuleExecutor:
    """Rule executor that processes context states and applies rules."""

    def __init__(self, mqtt_broker: str = "localhost", mqtt_port: int = 1883):
        """Initialize the rule executor."""
        self.service_id = "RuleExecutor"  # ID for this service, mainly for logging
        
        # Set up MQTT client with a unique client ID
        mqtt_client_id = f"{self.service_id}-{int(time.time()*1000)}"
        self.mqtt_client = mqtt.Client(client_id=mqtt_client_id)
        self.mqtt_client.on_connect = self._on_connect
        self.mqtt_client.on_message = self._on_message
        
        # Initialize RDF graph for rules
        self.rules_graph = Graph()
        # Optional: Define and bind your namespace if your SPARQL queries use prefixes like 'lapras:'
        # self.lapras_ns = Namespace("http://lapras.org/rule/")
        # self.rules_graph.bind("lapras", self.lapras_ns) # Use self.lapras_ns here
        
        try:
            self.mqtt_client.connect(mqtt_broker, mqtt_port)
        except Exception as e:
            logger.error(f"[{self.service_id}] MQTT connection error: {e}", exc_info=True)
            # Depending on desired behavior, you might raise this or try to reconnect.
            # For now, it will prevent loop_start if connection fails.
            raise  # Re-raise to make it clear initialization failed.

        # Start MQTT client loop
        self.mqtt_client.loop_start()
        logger.info(f"[{self.service_id}] Initialized with client ID: {mqtt_client_id} and connected to MQTT broker.")

    def _on_connect(self, client, userdata, flags, rc):
        """Callback when connected to MQTT broker."""
        if rc == 0:
            logger.info(f"[{self.service_id}] Successfully connected to MQTT broker.")
            # Subscribe to rule executor channel (where ContextManager publishes)
            topic_to_subscribe = "to_rule_executor"
            self.mqtt_client.subscribe(topic_to_subscribe)
            logger.info(f"[{self.service_id}] Subscribed to topic: {topic_to_subscribe}")
        else:
            logger.error(f"[{self.service_id}] Failed to connect to MQTT broker. Result code: {rc}")

    def _on_message(self, client, userdata, msg):
        """Callback when message is received from MQTT broker (from ContextManager)."""
        logger.debug(f"[{self.service_id}] Message received on topic '{msg.topic}'")
        try:
            payload_str = msg.payload.decode()
            data = json.loads(payload_str)
            context_map_from_cm = data.get("context_map", {})
            timestamp_from_cm = data.get("timestamp") # Timestamp from ContextManager's message

            if context_map_from_cm:
                logger.info(f"[{self.service_id}] Received context map with {len(context_map_from_cm)} agents for timestamp {timestamp_from_cm}")
                
                for agent_id_in_map, original_agent_state_from_cm in context_map_from_cm.items():
                    logger.info(f"[{self.service_id}] Processing rules for agent '{agent_id_in_map}'")
                    logger.debug(f"[{self.service_id}] State received from CM for '{agent_id_in_map}': {json.dumps(original_agent_state_from_cm, indent=2)}")
                    
                    state_after_rules_evaluation = self.evaluate_rules(agent_id_in_map, original_agent_state_from_cm)

                    if state_after_rules_evaluation is not None:
                        # Only publish if the state *actually changed* as a result of the rules.
                        if state_after_rules_evaluation != original_agent_state_from_cm:
                            message_to_agent = {
                                "agent_id": agent_id_in_map,
                                "state": state_after_rules_evaluation,
                                "timestamp": timestamp_from_cm # Pass on the relevant timestamp
                            }
                            publish_topic = "context_dist"
                            self.mqtt_client.publish(publish_topic, json.dumps(message_to_agent))
                            logger.info(f"[{self.service_id}] State for agent '{agent_id_in_map}' CHANGED by rules. Published new state to '{publish_topic}': {json.dumps(state_after_rules_evaluation, indent=2)}")
                        else:
                            logger.info(f"[{self.service_id}] Rules evaluated for agent '{agent_id_in_map}', but resulting state is IDENTICAL to original. No change published.")
                    else:
                        # evaluate_rules returned None (no applicable rules changed state or an error occurred)
                        logger.info(f"[{self.service_id}] No applicable rules resulted in a state change for agent '{agent_id_in_map}', or an error occurred in evaluation. Nothing published to context_dist.")
            # else:
                # logger.debug(f"[{self.service_id}] Received empty context map or 'context_map' key not found.")
        except json.JSONDecodeError:
            logger.error(f"[{self.service_id}] Message payload on topic '{msg.topic}' was not valid JSON: {msg.payload.decode(errors='ignore')}")
        except Exception as e:
            logger.error(f"[{self.service_id}] Error processing message in RuleExecutor._on_message: {e}", exc_info=True)

    def evaluate_rules(self, agent_id: str, current_state: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """
        Evaluate rules for a specific agent based on its current state.
        Handles rules with multiple conditions (all conditions for a rule must be ANDed).
        Returns the new state dictionary if any rule successfully applied a state_update, otherwise None.
        """
        try:
            agent_id_rdf_literal = rdflib.Literal(agent_id)
            agent_specific_rules_query = """
            SELECT DISTINCT ?rule_uri ?action_uri
            WHERE {
                ?rule_uri lapras:hasAgent ?agent_id_param .
                ?rule_uri lapras:hasAction ?action_uri .
            }
            """
            new_state_after_all_rules = current_state.copy()
            any_rule_led_to_actionable_change = False
            logger.debug(f"[{self.service_id}] Evaluating rules for agent '{agent_id}' with initial state: {json.dumps(current_state, indent=2)}")

            for rule_uri, action_uri in self.rules_graph.query(
                agent_specific_rules_query,
                initBindings={'agent_id_param': agent_id_rdf_literal}
            ):
                rule_name_short = str(rule_uri).split('/')[-1] or str(rule_uri)
                logger.info(f"[{self.service_id}] Considering rule '{rule_name_short}' (URI: {rule_uri}) for agent '{agent_id}'")

                rule_conditions_query = """
                SELECT ?condition_node
                WHERE { ?current_rule_param lapras:hasCondition ?condition_node . }
                """
                conditions_for_this_rule = list(self.rules_graph.query(
                    rule_conditions_query, initBindings={'current_rule_param': rule_uri}
                ))

                if not conditions_for_this_rule:
                    logger.info(f"[{self.service_id}] Rule '{rule_name_short}' has no 'lapras:hasCondition' predicates. Skipping.")
                    continue

                all_conditions_met_for_this_rule = True
                for i, condition_row in enumerate(conditions_for_this_rule):
                    condition_node = condition_row[0]
                    condition_name_short = str(condition_node).split('/')[-1] or str(condition_node)
                    logger.debug(f"[{self.service_id}]   Checking condition #{i+1} ('{condition_name_short}') for rule '{rule_name_short}'")
                    if not self._evaluate_condition(condition_node, current_state): # Pass original current_state
                        all_conditions_met_for_this_rule = False
                        logger.info(f"[{self.service_id}]   Condition '{condition_name_short}' for rule '{rule_name_short}' was FALSE. Rule will not trigger.")
                        break

                if all_conditions_met_for_this_rule:
                    logger.info(f"[{self.service_id}] Rule '{rule_name_short}' ALL {len(conditions_for_this_rule)} conditions met for agent '{agent_id}'")
                    action_data = self._parse_action(action_uri)
                    if action_data and "state_update" in action_data:
                        state_update_payload = action_data["state_update"]
                        if isinstance(state_update_payload, dict):
                            # Check if the state update actually changes anything
                            changed_by_this_action = False
                            for key, value in state_update_payload.items():
                                if new_state_after_all_rules.get(key) != value:
                                    changed_by_this_action = True
                                    break
                            
                            if changed_by_this_action:
                                logger.info(f"[{self.service_id}] Action for rule '{rule_name_short}' provides state_update: {json.dumps(state_update_payload, indent=2)}")
                                new_state_after_all_rules.update(state_update_payload)
                                any_rule_led_to_actionable_change = True
                                logger.info(f"[{self.service_id}] Agent '{agent_id}' state after rule '{rule_name_short}': {json.dumps(new_state_after_all_rules, indent=2)}")
                            else:
                                logger.info(f"[{self.service_id}] Action for rule '{rule_name_short}' triggered, but state_update {json.dumps(state_update_payload, indent=2)} does not change current aggregated state for '{agent_id}'.")
                        else:
                            logger.warning(f"[{self.service_id}] Rule '{rule_name_short}' state_update payload is not a dictionary: {state_update_payload}. Action URI: {action_uri}")
                    elif action_data is None:
                        logger.warning(f"[{self.service_id}] Rule '{rule_name_short}' conditions met, but _parse_action returned None. Action URI: {action_uri}")
                    else:
                        logger.warning(f"[{self.service_id}] Rule '{rule_name_short}' conditions met, but parsed action_data missing 'state_update'. Action URI: {action_uri}, Data: {action_data}")

            if any_rule_led_to_actionable_change:
                logger.info(f"[{self.service_id}] Final cumulative state for agent '{agent_id}' after all rules: {json.dumps(new_state_after_all_rules, indent=2)}")
                return new_state_after_all_rules
            else:
                logger.info(f"[{self.service_id}] No rules triggered an actual state_update for agent '{agent_id}'.")
                return None # No effective change means RuleExecutor._on_message should not publish
        except Exception as e:
            logger.error(f"[{self.service_id}] Critical error in evaluate_rules for agent '{agent_id}': {e}", exc_info=True)
            return None

    def _evaluate_condition(self, condition_node: rdflib.term.Node, current_agent_state: Dict[str, Any]) -> bool:
        """Evaluate a single condition definition against the agent's current state."""
        try:
            condition_query = """
            SELECT ?sensor_rdf ?operator_rdf ?value_rdf_node
            WHERE {
                ?condition_param lapras:hasSensor ?sensor_rdf .
                ?condition_param lapras:hasOperator ?operator_rdf .
                ?condition_param lapras:hasValue ?value_rdf_node .
            }"""
            # Assuming a condition node uniquely defines one sensor, operator, and value.
            # If a condition_node could have multiple, this query would need to be handled in a loop.
            # For now, taking the first result is implicit if there's only one.
            
            query_results = list(self.rules_graph.query(
                condition_query, initBindings={'condition_param': condition_node}
            ))

            if not query_results:
                logger.warning(f"[{self.service_id}] No specific (sensor, operator, value) found for condition node {condition_node} in RDF graph.")
                return False
            
            # Process the first (and assumed only) set of sensor/operator/value for this condition node
            sensor_rdf, operator_rdf, value_rdf_node = query_results[0]
            
            sensor_id_from_rule = str(sensor_rdf)
            operator_str = str(operator_rdf).split('/')[-1] # Extract operator name like 'equals', 'lessThan'
            
            # Get the value from the agent's current state that the rule refers to
            value_from_agent_state = current_agent_state.get(sensor_id_from_rule)

            if value_from_agent_state is None:
                logger.info(f"[{self.service_id}] Sensor '{sensor_id_from_rule}' (for condition '{condition_node}') not found in agent state: {current_agent_state}")
                return False # Condition cannot be met if sensor data is missing

            # Extract the Python value from the RDF literal defined in the rule
            rule_threshold_value = None
            if isinstance(value_rdf_node, rdflib.Literal):
                rule_threshold_value = value_rdf_node.value # .value gives Python native type
            else:
                logger.warning(f"[{self.service_id}] Rule value for sensor '{sensor_id_from_rule}' in condition '{condition_node}' is not an RDF Literal: {value_rdf_node}")
                return False
            
            logger.debug(f"[{self.service_id}]   Cond Check Details: Sensor='{sensor_id_from_rule}', Op='{operator_str}', AgentVal='{value_from_agent_state}' (type {type(value_from_agent_state).__name__}), RuleVal='{rule_threshold_value}' (type {type(rule_threshold_value).__name__})")
            
            result = False
            try:
                # Attempt numeric comparison first if operator suggests it
                if operator_str in ["greaterThan", "lessThan", "greaterThanOrEqual", "lessThanOrEqual"]:
                    val_state_num = float(value_from_agent_state)
                    rule_val_num = float(rule_threshold_value)
                    if operator_str == "greaterThan": result = val_state_num > rule_val_num
                    elif operator_str == "lessThan": result = val_state_num < rule_val_num
                    elif operator_str == "greaterThanOrEqual": result = val_state_num >= rule_val_num
                    elif operator_str == "lessThanOrEqual": result = val_state_num <= rule_val_num
                
                elif operator_str == "equals":
                    # Flexible equals: try numeric first if both seem numeric, else string
                    try:
                        val_state_num = float(value_from_agent_state)
                        rule_val_num = float(rule_threshold_value)
                        result = (val_state_num == rule_val_num)
                    except (ValueError, TypeError): # If conversion to float fails, fall back to string comparison
                        result = str(value_from_agent_state) == str(rule_threshold_value)
                
                elif operator_str == "notEquals":
                    try:
                        val_state_num = float(value_from_agent_state)
                        rule_val_num = float(rule_threshold_value)
                        result = (val_state_num != rule_val_num)
                    except (ValueError, TypeError):
                        result = str(value_from_agent_state) != str(rule_threshold_value)
                else:
                    logger.warning(f"[{self.service_id}] Unsupported operator '{operator_str}' for comparison in condition '{condition_node}'.")
                    return False
            except (ValueError, TypeError) as e:
                logger.warning(f"[{self.service_id}] Type error during comparison for sensor '{sensor_id_from_rule}' in condition '{condition_node}': {e}. AgentVal='{value_from_agent_state}', RuleVal='{rule_threshold_value}'")
                return False
            
            logger.debug(f"[{self.service_id}]   Condition evaluation: ('{value_from_agent_state}' {operator_str} '{rule_threshold_value}') = {result}")
            return result 
            
        except Exception as e:
            logger.error(f"[{self.service_id}] Error evaluating condition node '{condition_node}': {e}", exc_info=True)
            return False

    def _parse_action(self, action_uri: rdflib.term.Node) -> Optional[Dict[str, Any]]:
        """Parse an action node to find a state_update JSON string."""
        try:
            action_query = """
            SELECT ?state_update_json_str
            WHERE {
                ?action_param lapras:hasStateUpdate ?state_update_json_str .
            }
            """
            # Assuming one state_update per action node. If multiple, this takes the first.
            query_results = list(self.rules_graph.query(
                action_query, initBindings={'action_param': action_uri}
            ))

            if not query_results:
                logger.debug(f"[{self.service_id}] No 'lapras:hasStateUpdate' found for action URI: {action_uri}")
                return None
            
            state_update_json_str_literal = query_results[0][0] # Get the RDF Literal
            if not isinstance(state_update_json_str_literal, rdflib.Literal):
                logger.error(f"[{self.service_id}] Expected RDF Literal for state_update, got {type(state_update_json_str_literal)} for action {action_uri}")
                return None

            state_update_json_str = str(state_update_json_str_literal.value) # Get Python string from Literal

            try:
                parsed_state_update = json.loads(state_update_json_str)
                if not isinstance(parsed_state_update, dict):
                    logger.error(f"[{self.service_id}] 'state_update' for action {action_uri} is not a JSON object (dict): {state_update_json_str}")
                    return None
                return {"state_update": parsed_state_update}
            except json.JSONDecodeError as je:
                logger.error(f"[{self.service_id}] Invalid JSON in state_update for action {action_uri}: '{state_update_json_str}'. Error: {je}")
                return None
        
        except Exception as e:
            logger.error(f"[{self.service_id}] Error parsing action {action_uri}: {e}", exc_info=True)
            return None

    def load_rules(self, rdf_file_path: str) -> None:
        """Load rules from an RDF file."""
        try:
            self.rules_graph.parse(rdf_file_path, format="turtle")
            logger.info(f"[{self.service_id}] Successfully loaded rules from {rdf_file_path}")
            
            # Example: Log loaded rule details for verification
            rules_query = """
            SELECT ?rule_uri ?agent_literal (GROUP_CONCAT(?condition_uri; separator=", ") AS ?conditions) ?action_uri
            WHERE {
                ?rule_uri rdf:type lapras:Rule .
                ?rule_uri lapras:hasAgent ?agent_literal .
                OPTIONAL { ?rule_uri lapras:hasCondition ?condition_uri . }
                OPTIONAL { ?rule_uri lapras:hasAction ?action_uri . }
            }
            GROUP BY ?rule_uri ?agent_literal ?action_uri
            """
            logger.debug(f"[{self.service_id}] Verifying loaded rules:")
            for r_uri, ag_lit, conds_str, act_uri in self.rules_graph.query(rules_query):
                logger.debug(f"[{self.service_id}]   Rule: {r_uri}, Agent: {ag_lit}, Conditions: [{conds_str if conds_str else 'None'}], Action: {act_uri if act_uri else 'None'}")

        except Exception as e:
            logger.error(f"[{self.service_id}] Failed to load rules from {rdf_file_path}: {e}", exc_info=True)
            # Decide if this should halt the service or continue without rules
            # For now, it will continue, but evaluate_rules will find no rules.

    def stop(self) -> None:
        """Stop the rule executor."""
        logger.info(f"[{self.service_id}] Stopping rule executor...")
        if hasattr(self, 'mqtt_client') and self.mqtt_client:
            self.mqtt_client.loop_stop() # Stop the background thread
            self.mqtt_client.disconnect()
            logger.info(f"[{self.service_id}] MQTT client disconnected.")
        logger.info(f"[{self.service_id}] Rule executor stopped.")