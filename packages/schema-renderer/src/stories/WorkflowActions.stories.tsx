import React from "react";
import { WorkflowActions } from "../components/WorkflowActions";
import { WorkflowTransition } from "../types";

export default {
  title: "Components/WorkflowActions",
  component: WorkflowActions,
};

const draftTransitions: WorkflowTransition[] = [
  {
    name: "submit",
    from_state: "draft",
    to_state: "submitted",
    roles: ["admin", "editor"],
  },
];

const submittedTransitions: WorkflowTransition[] = [
  {
    name: "approve",
    from_state: "submitted",
    to_state: "approved",
    roles: ["admin"],
    has_conditions: true,
  },
  {
    name: "reject",
    from_state: "submitted",
    to_state: "rejected",
    roles: ["admin"],
  },
];

export const DraftState = () => (
  <div style={{ maxWidth: "600px", padding: "2rem" }}>
    <h3>Draft State (Submit Action Available)</h3>
    <WorkflowActions
      currentState="draft"
      availableTransitions={draftTransitions}
      onTransition={(name) => alert(`Triggering transition: ${name}`)}
    />
  </div>
);

export const SubmittedState = () => (
  <div style={{ maxWidth: "600px", padding: "2rem" }}>
    <h3>Submitted State (Approve with Guard Condition / Reject Actions)</h3>
    <WorkflowActions
      currentState="submitted"
      availableTransitions={submittedTransitions}
      onTransition={(name) => alert(`Triggering transition: ${name}`)}
    />
  </div>
);

export const ApprovedTerminalState = () => (
  <div style={{ maxWidth: "600px", padding: "2rem" }}>
    <h3>Approved State (Terminal or No Available Actions)</h3>
    <WorkflowActions
      currentState="approved"
      availableTransitions={[]}
      onTransition={(name) => alert(`Triggering transition: ${name}`)}
    />
  </div>
);

