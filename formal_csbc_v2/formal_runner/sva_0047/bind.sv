module bind_wrapper;
    // This module exists solely to give sby a top-level entry point.
    // The actual binding happens via the bind statement below.
endmodule

module sva_checker (
    input wire op_ack_o,
    input wire clk_i,
    input wire rst_ni
);

assert property (@(posedge clk_i) disable iff (!rst_ni) !$isunknown(op_ack_o));

endmodule


bind keymgr_op_state_ctrl sva_checker sva_checker_inst (.*);
