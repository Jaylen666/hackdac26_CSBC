module bind_wrapper;
    // This module exists solely to give sby a top-level entry point.
    // The actual binding happens via the bind statement below.
endmodule

module sva_checker (
    input wire cmd_consty_err_q,
    input wire clk_i,
    input wire rst_ni
);

assert property (@(posedge clk_i) disable iff (!rst_ni) (cmd_consty_err_q) |=> cmd_consty_err_q);

endmodule


bind keymgr_kmac_if sva_checker sva_checker_inst (.*);
