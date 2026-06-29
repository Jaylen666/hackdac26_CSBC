module bind_wrapper;
    // This module exists solely to give sby a top-level entry point.
    // The actual binding happens via the bind statement below.
endmodule

module sva_checker (
    input wire state_change_err_i,
    input wire op_state_cmd_err_i,
    input wire async_fault_o,
    input wire clk_i,
    input wire rst_ni
);

assert property (@(posedge clk_i) disable iff (!rst_ni) (state_change_err_i || op_state_cmd_err_i) |-> async_fault_o);

endmodule


bind keymgr_err sva_checker sva_checker_inst (.*);
