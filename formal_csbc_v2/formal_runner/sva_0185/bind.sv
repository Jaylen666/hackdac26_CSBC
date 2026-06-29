module bind_wrapper;
    // This module exists solely to give sby a top-level entry point.
    // The actual binding happens via the bind statement below.
endmodule

module sva_checker (
    input wire start,
    input wire done_o,
    input wire cmd_error_o,
    input wire inputs_invalid_o,
    input wire fsm_error_o,
    input wire kmac_data_o.data,
    input wire kmac_data_i.digest_share1,
    input wire kmac_data_i.digest_share0,
    input wire clk_i,
    input wire rst_ni
);

assert property (@(posedge clk_i) disable iff (!rst_ni) (start && done_o && !cmd_error_o && !inputs_invalid_o && !fsm_error_o) |-> (kmac_data_o.data == {kmac_data_i.digest_share1, kmac_data_i.digest_share0}));

endmodule


bind keymgr_kmac_if sva_checker sva_checker_inst (.*);
