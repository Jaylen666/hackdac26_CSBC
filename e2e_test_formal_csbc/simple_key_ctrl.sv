// Simplified key controller test case for Formal CSBC v2.0 validation
// Based on keymgr_ctrl ECC bug pattern (N-003)

module simple_key_ctrl (
  input  logic        clk_i,
  input  logic        rst_ni,
  input  logic        enable_i,
  input  logic        load_root_key_i,
  input  logic [63:0] root_key_share0_i,
  input  logic [63:0] root_key_share1_i,
  output logic [63:0] key_state_o,
  output logic        key_valid_o
);

  logic [63:0] key_state_q;
  logic [7:0]  key_state_ecc_q;

  // Bug: ECC width mismatch - should be [7:0] but assigned as scalar
  // This is the core of the N-003 bug pattern

  always_ff @(posedge clk_i or negedge rst_ni) begin
    if (!rst_ni) begin
      key_state_q <= 64'h0;
      key_state_ecc_q <= 8'h0;
    end else if (enable_i) begin
      if (load_root_key_i) begin
        key_state_q <= root_key_share0_i ^ root_key_share1_i;
        // BUG: This should compute proper ECC, but instead assigns scalar
        // The key_state might become zero if shares are identical
        {key_state_ecc_q} <= compute_ecc(root_key_share0_i ^ root_key_share1_i);
      end
    end
  end

  // Simplified ECC computation (just parity for demo)
  function automatic logic [7:0] compute_ecc(logic [63:0] data);
    return {^data[63:56], ^data[55:48], ^data[47:40], ^data[39:32],
            ^data[31:24], ^data[23:16], ^data[15:8], ^data[7:0]};
  endfunction

  // Bug manifestation: key_state can be zero when shares are identical
  // This violates security property: key should never be all-zero in enabled state
  assign key_state_o = key_state_q;
  assign key_valid_o = enable_i && (key_state_q != 64'h0);

  // Security property that should be checked:
  // When enabled and root key loaded, key_state should never be zero
  // assert property (@(posedge clk_i) disable iff (!rst_ni)
  //   (enable_i && load_root_key_i) |-> ##1 (key_state_q != 64'h0));

endmodule
