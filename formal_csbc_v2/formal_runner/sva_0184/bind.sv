module bind_wrapper;
    // This module exists solely to give sby a top-level entry point.
    // The actual binding happens via the bind statement below.
endmodule

module sva_checker (
    input wire cmd_consty_err_d,
    input wire cmd_consty_err_q,
    input wire rst_ni,
    input wire clock
);

assert property (@(posedge clock) (rst_ni == 0 && cmd_consty_err_d == 1) ##1 (rst_ni == 1) |-> (cmd_consty_err_q == 1));

endmodule


bind keymgr_kmac_if sva_checker sva_checker_inst (.*);
